"""Local, reviewable artifacts produced by a backtest run."""

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def write_backtest_artifacts(
    output_dir: Path, result: Dict[str, Any], run_id: int
) -> Dict[str, str]:
    """Write JSON, Markdown and CSV outputs without hiding missing-data warnings."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload["run_id"] = run_id

    result_path = output_dir / "result.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    report_path = output_dir / "report.md"
    report_path.write_text(_markdown_report(payload), encoding="utf-8")

    trades_path = output_dir / "trades.csv"
    _write_trades_csv(trades_path, payload.get("scenarios", {}))

    return {
        "result": str(result_path),
        "report": str(report_path),
        "trades": str(trades_path),
    }


def _markdown_report(result: Dict[str, Any]) -> str:
    provenance = result.get("provenance", {})
    lines = [
        "# FireAgent 回测报告",
        "",
        "## 回测身份",
        "",
        "- 运行 ID：{}".format(result.get("run_id", "")),
        "- 策略：{} @ {}".format(
            result.get("strategy_id", ""), result.get("strategy_version", "")
        ),
        "- 数据来源：{}".format(provenance.get("source_name", "未标注")),
        "- 数据来源版本：{}".format(provenance.get("source_version", "未标注")),
        "- a-stock-data Skill：{} {}".format(
            provenance.get("skill_name", "未标注"),
            provenance.get("skill_version", "未标注"),
        ),
        "- 数据区间：{} 至 {}".format(
            provenance.get("data_start", "未标注"),
            provenance.get("data_end", "未标注"),
        ),
        "- 频率：{}".format(provenance.get("frequency", "未标注")),
        "",
        "## 情景结果",
        "",
        "| 情景 | 最终权益 | 累计收益 | 最大回撤 | 完成交易数 | 胜率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, scenario in result.get("scenarios", {}).items():
        metrics = scenario.get("metrics", {})
        lines.append(
            "| {name} | {final} | {return_} | {drawdown} | {count} | {win_rate} |".format(
                name=name,
                final=metrics.get("final_equity", ""),
                return_=metrics.get("cumulative_return", ""),
                drawdown=metrics.get("max_drawdown", ""),
                count=metrics.get("trade_count", ""),
                win_rate=metrics.get("win_rate", ""),
            )
        )
    warnings = _unique_warnings(result.get("scenarios", {}).values())
    lines.extend(["", "## 数据与执行警告", ""])
    if warnings:
        lines.extend("- " + warning for warning in warnings)
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def _unique_warnings(scenarios: Iterable[Dict[str, Any]]) -> List[str]:
    warnings = set()
    for scenario in scenarios:
        warnings.update(scenario.get("warnings", []))
    return sorted(warnings)


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
