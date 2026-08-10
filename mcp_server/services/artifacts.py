"""Local, reviewable artifacts produced by a backtest run."""

import csv
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from mcp_server.services.charts import render_report_charts


def write_backtest_artifacts(
    output_dir: Path,
    result: Dict[str, Any],
    run_id: int,
    created_at: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Write one isolated artifact bundle and return its concrete paths."""

    result_payload = dict(result)
    result_payload["run_id"] = run_id
    artifact_dir = build_artifact_dir(output_dir, result_payload, run_id, created_at)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = artifact_dir / "charts"

    result_path = artifact_dir / "result.json"
    trades_path = artifact_dir / "trades.csv"

    chart_result = render_report_charts(result_payload, charts_dir)
    result_payload["artifact_warnings"] = list(chart_result.get("warnings", []))
    _atomic_write_text(
        result_path,
        json.dumps(result_payload, ensure_ascii=False, indent=2, sort_keys=True),
    )
    _write_trades_csv(trades_path, result_payload.get("scenarios", {}))
    analysis_payload = dict(analysis or {"status": "pending", "version": 1})
    analysis_path = artifact_dir / "analysis.json"
    _atomic_write_text(
        analysis_path,
        json.dumps(analysis_payload, ensure_ascii=False, indent=2, sort_keys=True),
    )
    report_path = artifact_dir / "report.md"
    _atomic_write_text(
        report_path,
        _markdown_report(result_payload, analysis_payload, chart_result),
    )
    return {
        "artifact_dir": str(artifact_dir),
        "result": str(result_path),
        "report": str(report_path),
        "trades": str(trades_path),
        "analysis": str(analysis_path),
        "charts": str(charts_dir),
    }


def build_artifact_dir(
    output_dir: Path,
    result: Dict[str, Any],
    run_id: int,
    created_at: Optional[str] = None,
) -> Path:
    value = _parse_created_at(created_at)
    timestamp = value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    strategy = _slug(result.get("strategy_id", "strategy"))
    version = _slug(result.get("strategy_version", "unknown"))
    return Path(output_dir) / "{}_{}_v{}_run-{}".format(timestamp, strategy, version, run_id)


def _markdown_report(
    result: Dict[str, Any], analysis: Dict[str, Any], chart_result: Dict[str, Any]
) -> str:
    provenance = result.get("provenance", {})
    lines = [
        "# 回测报告｜{}".format(result.get("strategy_id", "未命名策略")),
        "",
        "> 本报告只展示已保存回测事实；AI 观察单独列在文末，不修改事实结果。",
        "",
        "## 1. 回测身份与口径",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        "| 运行 ID | {} |".format(result.get("run_id", "")),
        "| 策略 | {} @ {} |".format(result.get("strategy_id", ""), result.get("strategy_version", "")),
        "| 模式 | {} |".format(result.get("run_mode", "未标注")),
        "| 数据区间 | {} 至 {} |".format(provenance.get("data_start", "未标注"), provenance.get("data_end", "未标注")),
        "| 数据来源 | {} |".format(provenance.get("source_name", "未标注")),
        "| Skill | {} {} |".format(provenance.get("skill_name", "未标注"), provenance.get("skill_version", "未标注")),
        "| 复权方式 | {} |".format(provenance.get("adjustment_method", provenance.get("price_basis", "未标注"))),
        "| 成交口径 | {} |".format(_execution_text(provenance.get("execution", {}))),
        "| 成本模板 | {} |".format(_cost_text(provenance.get("cost_profile", {}))),
        "| 基准选择 | {} |".format(_benchmark_text(result.get("assumptions", {}).get("benchmark"))),
        "| 年化无风险利率 | {} |".format(_percent(result.get("assumptions", {}).get("risk_free_rate_annual"))),
        "",
        "## 2. 核心指标",
        "",
        "| 情景 | 最终权益 | 净利润 | 累计收益率 | 年化收益率 | 年化波动率 | 最大回撤 | Sharpe | Sortino | Calmar |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, scenario in result.get("scenarios", {}).items():
        metrics = scenario.get("metrics", {})
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                name,
                _money(metrics.get("final_equity")),
                _money(metrics.get("net_profit")),
                _percent(metrics.get("cumulative_return", metrics.get("time_weighted_return"))),
                _percent(metrics.get("annualized_return")),
                _percent(metrics.get("annualized_volatility")),
                _percent(metrics.get("max_drawdown")),
                _number(metrics.get("sharpe_ratio")),
                _number(metrics.get("sortino_ratio")),
                _number(metrics.get("calmar_ratio")),
            )
        )
    lines.extend(["", "## 3. 基准与相对表现", ""])
    benchmark = result.get("benchmark_comparison")
    if not benchmark:
        lines.append("本次未选择基准，未计算相对表现。")
    elif benchmark.get("status") != "ok":
        lines.append("基准比较不可用：{}".format(benchmark.get("reason", "原因未记录")))
    else:
        lines.extend(
            [
                "| 指标 | 数值 |",
                "| --- | ---: |",
                "| 覆盖率 | {} |".format(_percent(benchmark.get("coverage"))),
                "| 策略区间收益 | {} |".format(_percent(benchmark.get("strategy_return"))),
                "| 基准区间收益 | {} |".format(_percent(benchmark.get("benchmark_return"))),
                "| 超额收益 | {} |".format(_percent(benchmark.get("excess_return"))),
                "| 跟踪误差 | {} |".format(_percent(benchmark.get("tracking_error"))),
                "| 信息比率 | {} |".format(_number(benchmark.get("information_ratio"))),
                "| Beta | {} |".format(_number(benchmark.get("beta"))),
                "| Alpha | {} |".format(_percent(benchmark.get("alpha"))),
            ]
        )
    lines.extend(["", "## 4. 图表", ""])
    paths = chart_result.get("paths", {})
    if paths.get("equity_drawdown"):
        lines.append("![净值与回撤](charts/equity_drawdown.png)")
    for key in sorted(paths):
        if key.startswith("monthly_returns_"):
            lines.append("![{}](charts/{}.png)".format(key, key))
    if paths.get("trade_pnl_distribution"):
        lines.append("![交易盈亏分布](charts/trade_pnl_distribution.png)")
    if chart_result.get("warnings"):
        lines.extend(["", "图表警告："])
        lines.extend("- " + warning for warning in chart_result["warnings"])
    lines.extend(["", "## 5. 年度与月度表现", ""])
    for name, scenario in result.get("scenarios", {}).items():
        metrics = scenario.get("metrics", {})
        lines.append("### {}".format(name))
        lines.append("")
        lines.append("年度收益：{}".format(_period_text(metrics.get("annual_returns", {}))))
        lines.append("")
        lines.append("月度收益：{}".format(_period_text(metrics.get("monthly_returns", {}))))
        lines.append("")
    lines.extend(["## 6. 交易、成本与持仓", ""])
    lines.extend(_trade_section(result))
    layered = provenance.get("layered") or {}
    ladder = layered.get("drawdown_ladder") or {}
    fibonacci = layered.get("fibonacci_ladder") or {}
    if layered:
        core = layered.get("core") or {}
        lines.extend(
            [
                "",
                "### Ladder assumptions",
                "",
                "- Exit mode: {}.".format(layered.get("exit_mode", "rsi")),
                "- Sell basis: {}.".format(layered.get("sell_basis", "all_tactical")),
                "- Core ratio: {}; trigger: {}; hold: {}.".format(
                    core.get("ratio"), core.get("trigger"), core.get("hold")
                ),
                (
                    "- Drawdown ladder: anchor window {}; thresholds {}; amounts {}; MA{} boost +1/+2; combine {}.".format(
                        ladder.get("anchor_window"),
                        ladder.get("thresholds"),
                        ladder.get("amounts"),
                        ladder.get("annual_period"),
                        ladder.get("combine", "max"),
                    )
                    if ladder
                    else "- Fibonacci ladder: prior {} completed bars; ratios {}; amounts {}; crossing {}.".format(
                        fibonacci.get("anchor_window"),
                        fibonacci.get("ratios"),
                        fibonacci.get("amounts"),
                        fibonacci.get("crossing", "first_close_below"),
                    )
                ),
            ]
        )
    lines.extend(["", "## 7. 样本内外与滚动验证", ""])
    lines.extend(_validation_section(result.get("validation", {})))
    lines.extend(["", "## 8. 数据与执行问题", ""])
    lines.extend(_warning_section(result))
    lines.extend(["", "## 9. AI 观察与候选实验", ""])
    lines.extend(_analysis_section(analysis))
    lines.extend(["", "## 10. 产物与指标说明", ""])
    lines.extend(
        [
            "- 原始确定性结果：`result.json`。",
            "- 成交及未成交明细：`trades.csv`。",
            "- AI 分析快照：`analysis.json`。",
            "- 年化收益使用现金流调整后的时间加权收益和实际日历跨度；波动率类指标按 252 个交易日年化。",
            "- 缺少字段、样本不足或旧结果未记录的指标显示为不可用，不用猜测值补齐。",
        ]
    )
    return "\n".join(lines) + "\n"


def _trade_section(result):
    lines = []
    for name, scenario in result.get("scenarios", {}).items():
        metrics = scenario.get("metrics", {})
        lines.extend(
            [
                "### {}".format(name),
                "",
                "- 已实现卖出：{}；买入成交：{}；卖出成交：{}；未成交：{}；成交率：{}".format(
                    metrics.get("realized_sell_count", metrics.get("trade_count", "不可用")),
                    metrics.get("buy_fill_count", "不可用"),
                    metrics.get("sell_fill_count", "不可用"),
                    metrics.get("unfilled_order_count", "不可用"),
                    _percent(metrics.get("fill_rate")),
                ),
                "- 胜率：{}；利润因子：{}；盈亏比：{}；期望收益：{}".format(
                    _percent(metrics.get("win_rate")),
                    _number(metrics.get("profit_factor")),
                    _number(metrics.get("payoff_ratio")),
                    _money(metrics.get("expectancy")),
                ),
                "- 平均/最大盈利：{}/{}；平均/最大亏损：{}/{}；最大连续盈利/亏损：{}/{}".format(
                    _money(metrics.get("average_win")),
                    _money(metrics.get("largest_win")),
                    _money(metrics.get("average_loss")),
                    _money(metrics.get("largest_loss")),
                    metrics.get("max_consecutive_wins", "不可用"),
                    metrics.get("max_consecutive_losses", "不可用"),
                ),
                "- 成交金额：{}；总费用：{}；换手率：{}；平均持有天数：{}".format(
                    _money(metrics.get("gross_notional")),
                    _money(metrics.get("total_fees")),
                    _number(metrics.get("turnover")),
                    _number(metrics.get("average_holding_days")),
                ),
                "- 费用拆分：佣金 {}；印花税 {}；过户费 {}；滑点影响 {}。".format(
                    _money(metrics.get("commission")),
                    _money(metrics.get("stamp_duty")),
                    _money(metrics.get("transfer_fee")),
                    _money(metrics.get("slippage_impact")),
                ),
                "- 仓位：平均 {}；最高 {}；在场时间 {}；当前现金 {}；当前持仓市值 {}。".format(
                    _percent(metrics.get("average_exposure")),
                    _percent(metrics.get("max_exposure")),
                    _percent(metrics.get("time_in_market_ratio")),
                    _money(metrics.get("current_cash")),
                    _money(metrics.get("current_market_value")),
                ),
                "- Cash-neutral XIRR: cumulative {}; annualized {}; active sessions {}.".format(
                    _percent(metrics.get("cash_neutral_cumulative_return")),
                    _percent(metrics.get("cash_neutral_annualized_return")),
                    metrics.get("cash_neutral_active_sessions", "unavailable"),
                ),
                "- Cash-neutral TWR: cumulative {}; annualized {}; active calendar days {}; max drawdown {}; peak {}; trough {}; recovery {}.".format(
                    _percent(metrics.get("cash_neutral_twr_cumulative_return")),
                    _percent(metrics.get("cash_neutral_twr_annualized_return")),
                    metrics.get("cash_neutral_active_calendar_days", "unavailable"),
                    _percent(metrics.get("cash_neutral_max_drawdown")),
                    metrics.get("cash_neutral_max_drawdown_peak_date", "unavailable"),
                    metrics.get("cash_neutral_max_drawdown_trough_date", "unavailable"),
                    metrics.get("cash_neutral_max_drawdown_recovery_date")
                    or "not recovered by end",
                ),
                "- 当前持仓数量：{}；可卖数量：{}".format(
                    metrics.get("current_position_quantity", "不可用"),
                    metrics.get("current_available_quantity", "不可用"),
                ),
            ]
        )
        if "core_position_quantity" in metrics:
            skipped = scenario.get("skipped_sell_signals")
            skipped_count = (
                len(skipped)
                if skipped is not None
                else int(metrics.get("skipped_sell_signal_count", 0) or 0)
            )
            lines.append(
                "- Layered holdings: core quantity {}; tactical quantity {}; core market value {}; tactical market value {}; cash {}.".format(
                    metrics.get("core_position_quantity"),
                    metrics.get("tactical_position_quantity"),
                    _money(metrics.get("core_market_value")),
                    _money(metrics.get("tactical_market_value")),
                    _money(metrics.get("layered_cash")),
                )
            )
            lines.append(
                "- Skipped sell signals: {}.".format(
                    skipped_count
                )
            )
    return lines


def _validation_section(validation):
    if not validation:
        return ["- 未记录样本内外或滚动验证。"]
    lines = []
    split = validation.get("sample_split") or {}
    if split:
        lines.append("- 样本切分比例：{}；训练区间：{} 至 {}；测试区间：{} 至 {}。".format(
            split.get("ratio", "不可用"),
            split.get("train_start", "不可用"),
            split.get("train_end", "不可用"),
            split.get("test_start", "不可用"),
            split.get("test_end", "不可用"),
        ))
        for label in ("train", "test"):
            values = split.get(label, {}).get("default", {})
            if values:
                lines.append("- {}：收益 {}，最大回撤 {}，交易 {}，胜率 {}。".format(
                    "训练" if label == "train" else "测试",
                    _percent(values.get("time_weighted_return", values.get("cumulative_return"))),
                    _percent(values.get("max_drawdown")),
                    values.get("trade_count", "不可用"),
                    _percent(values.get("win_rate")),
                ))
    rolling = validation.get("rolling") or {}
    if rolling:
        lines.append("- 滚动验证：训练 {} 年、测试 {} 年、窗口 {} 个。".format(
            rolling.get("train_years", "不可用"),
            rolling.get("test_years", "不可用"),
            len(rolling.get("windows", [])),
        ))
    return lines or ["- 未记录样本内外或滚动验证。"]


def _warning_section(result):
    groups = _group_warnings(result)
    if not groups:
        return ["- 无。"]
    lines = ["| 类别 | 次数 | 示例 |", "| --- | ---: | --- |"]
    for category, values in sorted(groups.items()):
        examples = "；".join(values[:3]).replace("|", "\\|")
        lines.append("| {} | {} | {} |".format(category, len(values), examples))
    return lines


def _analysis_section(analysis):
    if not analysis or analysis.get("status", "pending") == "pending":
        return ["AI 分析待生成；回测事实已保存，需由 Agent 读取分析上下文后补充。"]
    lines = []
    for title, key in (("结果概述", "summary"), ("优势", "strengths"), ("风险", "risks"), ("数据与执行限制", "data_limitations"), ("候选实验", "experiments")):
        lines.extend(["### {}".format(title), ""])
        values = analysis.get(key) or []
        if isinstance(values, str):
            values = [values]
        if not values:
            lines.append("- 无。")
        else:
            for value in values:
                if isinstance(value, dict):
                    text = value.get("text") or value.get("title") or json.dumps(value, ensure_ascii=False)
                    refs = value.get("evidence_refs") or []
                    lines.append("- {}（证据：{}）".format(text, ", ".join(refs) or "未提供"))
                else:
                    lines.append("- " + str(value))
        lines.append("")
    return lines[:-1]


def _group_warnings(result):
    groups: Dict[str, List[str]] = {}
    warnings = list(result.get("warnings", []))
    for scenario in result.get("scenarios", {}).values():
        warnings.extend(scenario.get("warnings", []))
        for trade in scenario.get("trades", []):
            if trade.get("status") == "UNFILLED":
                warnings.append(trade.get("reason", "订单未成交"))
    for warning in sorted(set(str(value) for value in warnings if value)):
        category = _warning_category(warning)
        groups.setdefault(category, []).append(warning)
    return groups


def _warning_category(value):
    if "没有可卖" in value or "可卖" in value or "T+1" in value:
        return "持仓/T+1"
    if "停牌" in value:
        return "停牌"
    if "涨停" in value or "跌停" in value:
        return "涨跌停"
    if "数据" in value or "缺失" in value:
        return "数据质量"
    if "交易单位" in value or "数量" in value:
        return "交易单位"
    return "其他执行"


def _execution_text(value):
    if not value:
        return "未标注"
    return "信号 {}，成交 {}".format(value.get("signal_at", "未标注"), value.get("fill_at", "未标注"))


def _cost_text(value):
    if not value:
        return "未标注"
    return "{} @ {}".format(value.get("template", "未标注"), value.get("version", "未标注"))


def _period_text(values):
    return "；".join("{}: {}".format(key, _percent(value)) for key, value in values.items()) or "不可用"


def _money(value):
    return "不可用" if value is None else "{:,.2f}".format(float(value))


def _percent(value):
    return "不可用" if value is None else "{:.2f}%".format(float(value) * 100)


def _number(value):
    return "不可用" if value is None else "{:.4f}".format(float(value))


def _benchmark_text(value):
    if not value:
        return "不使用基准"
    return "{} {}（{}）".format(
        value.get("code", "未标注"),
        value.get("name", "未标注"),
        value.get("instrument_type", "未标注"),
    )


def _slug(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "strategy"))
    return value.strip("-.")[:80] or "strategy"


def _parse_created_at(value):
    if not value:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed


def _write_trades_csv(path: Path, scenarios: Dict[str, Dict[str, Any]]) -> None:
    fields = [
        "scenario",
        "code",
        "side",
        "date",
        "signal_date",
        "price",
        "quantity",
        "status",
        "pnl",
        "fee",
        "holding_days",
        "reason",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for scenario_name, scenario in scenarios.items():
            for trade in scenario.get("trades", []):
                row = dict(trade)
                row["scenario"] = scenario_name
                writer.writerow({field: row.get(field, "") for field in fields})


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent), delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
