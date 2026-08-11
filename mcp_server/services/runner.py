"""Application service for one idempotent daily report run."""

from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from mcp_server.calendar import TradingCalendar
from mcp_server.domain.models import RunResult
from mcp_server.services.reporting import DailyReportBuilder, write_daily_report


class DailyReportRunner:
    def __init__(
        self,
        store,
        market_provider,
        notifier=None,
        calendar: Optional[TradingCalendar] = None,
        report_builder: Optional[DailyReportBuilder] = None,
        strategy_signal_builder: Optional[Callable] = None,
        report_dir=None,
        now_fn: Optional[Callable[[], datetime]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
        require_authoritative_calendar: bool = False,
    ):
        self.store = store
        self.market_provider = market_provider
        self.notifier = notifier
        self.calendar = calendar or TradingCalendar()
        self.report_builder = report_builder or DailyReportBuilder()
        self.strategy_signal_builder = strategy_signal_builder
        self.report_dir = report_dir
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.sleep_fn = sleep_fn or __import__("time").sleep
        self.require_authoritative_calendar = require_authoritative_calendar

    def run(self, report_date: Optional[date] = None, send: bool = True) -> RunResult:
        schedule = self.store.get_daily_report_schedule()
        timezone_name = schedule.timezone or "Asia/Shanghai"
        try:
            zone = ZoneInfo(timezone_name)
        except Exception as exc:
            if send:
                return RunResult(
                    status="blocked_invalid_timezone",
                    message="日报时区无效：{}".format(exc),
                )
            zone = ZoneInfo("Asia/Shanghai")
        target_date = report_date or self._local_now(zone).date()
        if (
            send
            and self.require_authoritative_calendar
            and not self.calendar.is_authoritative
        ):
            return RunResult(
                status="blocked_calendar_unavailable",
                message=(
                    "无法确认 A 股交易日历，已阻止定时推送；请安装 exchange-calendars "
                    "或配置工作区 data/trading_holidays.json"
                ),
            )
        if schedule.trading_days_only and not self.calendar.is_trading_day(target_date):
            return RunResult(
                status="skipped_non_trading_day",
                message="{} 不是交易日".format(target_date.isoformat()),
            )
        if send:
            gate = self._wait_for_send_window(target_date, schedule, zone)
            if gate is not None:
                return gate

        items = self.store.list_watchlist()
        if send and not items:
            return RunResult(
                status="skipped_empty_watchlist",
                message="观察清单为空，未发送日报",
            )
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

        claimed = self.store.claim_report_run(
            idempotency_key=idempotency_key,
            report_date=target_date,
            session="morning_close",
        )
        if not claimed.get("claimed"):
            return RunResult(
                status="already_running",
                report_id=claimed.get("id"),
                message="相同日报已经由另一个运行器处理",
            )
        run_id = claimed["id"]
        collection_time = self._local_now(zone)
        cutoff = "午间行情 {}".format(collection_time.strftime("%H:%M"))
        try:
            snapshots = self._snapshots(items, cutoff, target_date)
        except Exception as exc:
            snapshots = []
            for item in items:
                from mcp_server.adapters.a_stock_data import _missing_snapshot

                snapshots.append(
                    _missing_snapshot(
                        item, str(exc), getattr(self.market_provider, "skill", None)
                    )
                )

        strategy_signals = []
        if self.strategy_signal_builder is not None:
            try:
                strategy_signals = self.strategy_signal_builder(
                    items, snapshots, target_date
                )
            except Exception as exc:
                strategy_signals = [
                    {
                        "status": "unavailable",
                        "action": "UNDETERMINED",
                        "mode": "morning_close_approximation",
                        "error": str(exc),
                    }
                ]
        report = self.report_builder.build(
            target_date,
            cutoff,
            snapshots,
            strategy_signals=strategy_signals,
        )
        if self.report_dir is not None:
            write_daily_report(self.report_dir, target_date, report.content)
        self.store.update_report_run(
            run_id,
            report.status,
            data_as_of=report.data_as_of.isoformat() if report.data_as_of else None,
            content=report.content,
        )

        if not send:
            self.store.update_report_run(run_id, "previewed")
            return RunResult("previewed", run_id, report.content)
        if self.notifier is None:
            error = "通知渠道未配置，日报已生成但未发送"
            self.store.update_report_run(run_id, "delivery_failed", error=error)
            return RunResult("notification_not_configured", run_id, error)

        delivery = self._send(report)
        success = delivery is True or bool(getattr(delivery, "success", False))
        attempts = int(getattr(delivery, "attempts", 1))
        response_code = getattr(delivery, "response_code", None)
        error = getattr(delivery, "error", None)
        records = getattr(delivery, "attempt_records", None) or [
            {
                "chunk_index": 0,
                "chunk_count": int(getattr(delivery, "chunks_total", 1) or 1),
                "attempt": attempts,
                "status": "sent" if success else "failed",
                "response_code": response_code,
                "error": error,
                "content_format": getattr(delivery, "content_format", "text"),
            }
        ]
        for record in records:
            self.store.record_delivery_attempt(
                run_id=run_id,
                channel_id="feishu-main",
                attempt=record.get("attempt", attempts),
                status=record.get("status", "failed"),
                response_code=record.get("response_code"),
                error=record.get("error"),
                chunk_index=record.get("chunk_index", 0),
                chunk_count=record.get("chunk_count", 1),
                content_format=record.get("content_format", "text"),
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

    def _send(self, report):
        if hasattr(self.notifier, "send_report"):
            return self.notifier.send_report(report)
        if hasattr(self.notifier, "send_markdown"):
            return self.notifier.send_markdown(report.content)
        return self.notifier.send(report.content)

    def _snapshots(self, items, cutoff, target_date):
        try:
            return self.market_provider.snapshots_for(
                items, cutoff, report_date=target_date
            )
        except TypeError as exc:
            if "report_date" not in str(exc):
                raise
            return self.market_provider.snapshots_for(items, cutoff)

    def _local_now(self, zone: ZoneInfo) -> datetime:
        value = self.now_fn()
        if value.tzinfo is None:
            value = value.replace(tzinfo=zone)
        return value.astimezone(zone)

    def _wait_for_send_window(self, target_date, schedule, zone):
        try:
            start = schedule.send_start
            end = schedule.send_end
            if start > end:
                return RunResult(
                    status="blocked_invalid_schedule",
                    message="日报发送起止时间无效",
                )
            if not schedule.enabled:
                return RunResult(
                    status="skipped_disabled",
                    message="日报调度已禁用",
                )
            current = self._local_now(zone)
            if current.date() != target_date:
                return RunResult(
                    status="blocked_report_date",
                    message="运行日期 {} 与报告日期 {} 不一致".format(
                        current.date().isoformat(), target_date.isoformat()
                    ),
                )
            start_at = current.replace(
                hour=start.hour,
                minute=start.minute,
                second=0,
                microsecond=0,
            )
            end_at = current.replace(
                hour=end.hour,
                minute=end.minute,
                second=59,
                microsecond=999999,
            )
            while current < start_at:
                seconds = max(1.0, (start_at - current).total_seconds())
                self.sleep_fn(min(seconds, 60.0))
                current = self._local_now(zone)
            if current > end_at:
                return RunResult(
                    status="missed_window",
                    message="已超过日报发送窗口 {}–{}".format(
                        start.isoformat(timespec="minutes"),
                        end.isoformat(timespec="minutes"),
                    ),
                )
            return None
        except Exception as exc:
            return RunResult(
                status="blocked_invalid_schedule",
                message="日报发送窗口无效：{}".format(exc),
            )
