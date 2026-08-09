"""Deterministic, evidence-labelled Markdown report generation."""

from datetime import date, datetime
from typing import Iterable, List, Optional

from mcp_server.domain.models import DailyReport, MarketSnapshot


class DailyReportBuilder:
    def build(
        self,
        report_date: date,
        cutoff: str,
        snapshots: Iterable[MarketSnapshot],
    ) -> DailyReport:
        items = list(snapshots)
        statuses = [item.status for item in items]
        status = "ok" if items and all(value == "ok" for value in statuses) else "partial"
        data_times = [item.as_of for item in items if item.as_of is not None]
        data_as_of = max(data_times) if data_times else None

        lines = [
            "# A股午间观察日报",
            "",
            "> 日期：{}；截至{}；本报告只使用上午盘数据。".format(
                report_date.isoformat(), cutoff
            ),
            "",
        ]
        if not items:
            lines.append("观察清单为空，未生成标的数据。")
        else:
            for index, item in enumerate(items):
                lines.extend(_render_item(item))
                if index != len(items) - 1:
                    lines.append("---")
        lines.extend(
            [
                "",
                "> 说明：估值指标沿用数据源最近可用口径；缺失值不会用 0 或旧值填充。",
            ]
        )
        return DailyReport(
            report_date=report_date,
            cutoff=cutoff,
            content="\n".join(lines),
            data_as_of=data_as_of,
            status=status,
        )


def _render_item(item: MarketSnapshot) -> List[str]:
    lines = [
        "## {}（{}，{}）".format(item.name or "未命名标的", item.code, item.instrument_type),
        "- 行情：{}；上午涨跌：{}；成交额：{}；换手率：{}".format(
            _number(item.price, 3, "元"),
            _percent(item.change_pct),
            _number(item.amount_wan, 1, "万"),
            _percent(item.turnover_pct),
        ),
        "- 估值：PE(TTM) {}；PB {}".format(
            _multiple(item.pe_ttm), _multiple(item.pb)
        ),
    ]
    if item.signals:
        lines.append("- 规则信号：{}".format("；".join(item.signals)))
    else:
        lines.append("- 规则信号：暂无配置或未触发")

    data_time = item.as_of.isoformat() if item.as_of else "缺失"
    source = item.source_name or "未知来源"
    if item.source_url:
        source = "[{}]({})".format(source, item.source_url)
    lines.append("- 数据时间：{}；来源：{}；状态：{}".format(data_time, source, item.status))
    if item.skill_name or item.skill_version:
        lines.append(
            "- 数据 Skill：{}；版本：{}".format(
                item.skill_name or "缺失", item.skill_version or "缺失"
            )
        )

    for warning in item.warnings:
        lines.append("- ⚠️ {}".format(warning))
    for error in item.errors:
        lines.append("- ❌ {}".format(error))
    if item.status != "ok" and not item.warnings and not item.errors:
        lines.append("- ⚠️ 数据缺失：该标的没有可用的完整行情或估值字段。")
    return lines


def _number(value: Optional[float], digits: int, suffix: str) -> str:
    if value is None:
        return "数据缺失"
    return "{:.{}f}{}".format(value, digits, suffix)


def _percent(value: Optional[float]) -> str:
    if value is None:
        return "数据缺失"
    return "{:.2f}%".format(value)


def _multiple(value: Optional[float]) -> str:
    if value is None or value <= 0:
        return "数据缺失"
    return "{:.2f}x".format(value)
