"""Deterministic, evidence-labelled Markdown report generation."""

from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional

from mcp_server.domain.models import DailyReport, MarketSnapshot


class DailyReportBuilder:
    def build(
        self,
        report_date: date,
        cutoff: str,
        snapshots: Iterable[MarketSnapshot],
        strategy_signals: Optional[Iterable[dict]] = None,
    ) -> DailyReport:
        items = list(snapshots)
        signals = list(strategy_signals or [])
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
        if signals:
            lines.extend(["", "## 策略信号", ""])
            for signal in signals:
                lines.extend(_render_strategy_signal(signal))
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
            snapshots=items,
        )


def write_daily_report(report_dir: Path, report_date: date, content: str) -> Path:
    """Persist one local Markdown report without replacing prior dates."""

    target_dir = Path(report_dir) / "daily"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "{}.md".format(report_date.isoformat())
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return target


def _render_strategy_signal(signal: dict) -> List[str]:
    status = signal.get("status", "unknown")
    strategy_id = signal.get("strategy_id", "unknown")
    strategy_version = signal.get("strategy_version", "unknown")
    action = signal.get("action", "UNDETERMINED")
    mode = signal.get("mode", "unknown")
    details = signal.get("signal") or {}
    state = signal.get("state") or {}
    evidence = details.get("evidence") or {}
    lines = [
        "- 标的：{}；动作：{}；状态：{}".format(
            signal.get("code", "512890"), action, status
        ),
        "- 策略：{} @ {}；运行口径：{}".format(
            strategy_id, strategy_version, mode
        ),
        "- 买入金额：{}；卖出数量：{}；模拟现金：{}；总数量：{}".format(
            details.get("buy_cash", 0),
            details.get("sell_quantity", 0),
            state.get("cash", "不可用"),
            state.get("total_quantity", "不可用"),
        ),
    ]
    if evidence.get("indicator_values"):
        lines.append("- 指标：{}".format(evidence["indicator_values"]))
    if evidence.get("morning_price") is not None:
        lines.append("- 上午临时收盘价：{}（仅为运营近似，不改变正式回测策略）".format(evidence["morning_price"]))
    return lines


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
