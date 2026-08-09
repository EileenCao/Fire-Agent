"""Application service for one idempotent daily report run."""

from datetime import date
from typing import Optional

from mcp_server.calendar import TradingCalendar
from mcp_server.domain.models import RunResult
from mcp_server.services.reporting import DailyReportBuilder


class DailyReportRunner:
    def __init__(
        self,
        store,
        market_provider,
        notifier=None,
        calendar: Optional[TradingCalendar] = None,
        report_builder: Optional[DailyReportBuilder] = None,
    ):
        self.store = store
        self.market_provider = market_provider
        self.notifier = notifier
        self.calendar = calendar or TradingCalendar()
        self.report_builder = report_builder or DailyReportBuilder()

    def run(self, report_date: Optional[date] = None, send: bool = True) -> RunResult:
        target_date = report_date or date.today()
        if not self.calendar.is_trading_day(target_date):
            return RunResult(
                status="skipped_non_trading_day",
                message="{} 不是交易日".format(target_date.isoformat()),
            )

        items = self.store.list_watchlist()
        idempotency_key = "daily_watchlist:{}:morning_close:{}".format(
            target_date.isoformat(), self.store.watchlist_version()
        )
        previous = self.store.get_report_run(idempotency_key)
        if previous and previous.get("status") == "sent":
            return RunResult(
                status="already_sent",
                report_id=previous.get("id"),
                message="相同观察清单的日报已经发送",
            )

        cutoff = "上午收盘 11:30"
        try:
            snapshots = self.market_provider.snapshots_for(items, cutoff)
        except Exception as exc:
            snapshots = []
            for item in items:
                from mcp_server.adapters.a_stock_data import _missing_snapshot

                snapshots.append(
                    _missing_snapshot(
                        item, str(exc), getattr(self.market_provider, "skill", None)
                    )
                )

        report = self.report_builder.build(target_date, cutoff, snapshots)
        row = self.store.create_report_run(
            idempotency_key=idempotency_key,
            report_date=target_date,
            session="morning_close",
            data_as_of=report.data_as_of.isoformat() if report.data_as_of else None,
            status=report.status,
            content=report.content,
        )
        run_id = row["id"]

        if not send or self.notifier is None:
            self.store.update_report_run(run_id, "previewed")
            return RunResult("previewed", run_id, report.content)

        delivery = self._send(report.content)
        success = delivery is True or bool(getattr(delivery, "success", False))
        attempts = int(getattr(delivery, "attempts", 1))
        response_code = getattr(delivery, "response_code", None)
        error = getattr(delivery, "error", None)
        self.store.record_delivery_attempt(
            run_id=run_id,
            channel_id="feishu-main",
            attempt=attempts,
            status="sent" if success else "failed",
            response_code=response_code,
            error=error,
        )
        self.store.update_report_run(
            run_id,
            "sent" if success else "delivery_failed",
            error=None if success else (error or "通知渠道返回失败"),
        )
        return RunResult(
            status="sent" if success else "delivery_failed",
            report_id=run_id,
            message=report.content if success else (error or "通知发送失败"),
        )

    def _send(self, content: str):
        if hasattr(self.notifier, "send_markdown"):
            return self.notifier.send_markdown(content)
        return self.notifier.send(content)
