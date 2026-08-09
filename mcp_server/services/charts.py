"""Optional static chart rendering for Markdown backtest reports."""

from pathlib import Path
from typing import Any, Dict, Iterable, List


def render_report_charts(result: Dict[str, Any], charts_dir: Path) -> Dict[str, Any]:
    charts_dir = Path(charts_dir)
    charts_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        return {"paths": {}, "warnings": ["图表依赖不可用：{}".format(exc)]}

    paths: Dict[str, str] = {}
    warnings: List[str] = []
    try:
        paths["equity_drawdown"] = str(
            _equity_drawdown_chart(result, charts_dir, plt)
        )
    except Exception as exc:
        warnings.append("净值/回撤图生成失败：{}".format(exc))
    for scenario_name, scenario in result.get("scenarios", {}).items():
        try:
            paths["monthly_returns_{}".format(scenario_name)] = str(
                _monthly_returns_chart(scenario, scenario_name, charts_dir, plt)
            )
        except Exception as exc:
            warnings.append("{} 月度收益图生成失败：{}".format(scenario_name, exc))
    try:
        paths["trade_pnl_distribution"] = str(
            _trade_pnl_chart(result, charts_dir, plt)
        )
    except Exception as exc:
        warnings.append("交易盈亏图生成失败：{}".format(exc))
    return {"paths": paths, "warnings": warnings}


def _equity_drawdown_chart(result, charts_dir, plt):
    figure, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for name, scenario in result.get("scenarios", {}).items():
        equity = _series(scenario.get("equity_curve", {}))
        if not equity:
            continue
        dates, values = zip(*equity)
        base = values[0] or 1.0
        normalized = [value / base for value in values]
        axes[0].plot(dates, normalized, label=name)
        drawdowns = _drawdowns(values)
        axes[1].plot(dates, drawdowns, label=name)
    benchmark = _series(result.get("benchmark_equity_curve", {}))
    if benchmark:
        dates, values = zip(*benchmark)
        base = values[0] or 1.0
        axes[0].plot(
            dates,
            [value / base for value in values],
            label="benchmark",
            linestyle="--",
            color="#555555",
        )
        axes[1].plot(
            dates,
            _drawdowns(values),
            label="benchmark",
            linestyle="--",
            color="#555555",
        )
    axes[0].set_title("Equity Curve (Normalized)")
    axes[0].set_ylabel("Equity")
    axes[1].set_title("Drawdown")
    axes[1].set_ylabel("Drawdown")
    axes[1].set_xlabel("Date")
    for axis in axes:
        axis.grid(alpha=0.25)
        axis.legend()
        axis.tick_params(axis="x", rotation=30)
    figure.tight_layout()
    path = charts_dir / "equity_drawdown.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def _monthly_returns_chart(scenario, scenario_name, charts_dir, plt):
    values = scenario.get("metrics", {}).get("monthly_returns", {})
    figure, axis = plt.subplots(figsize=(12, 3.5))
    if values:
        labels = list(values)
        numbers = [float(values[label]) * 100 for label in labels]
        colors = ["#2e8b57" if value >= 0 else "#c0392b" for value in numbers]
        axis.bar(labels, numbers, color=colors)
        axis.axhline(0, color="#333333", linewidth=0.8)
        axis.set_ylabel("Return (%)")
        axis.tick_params(axis="x", rotation=60)
    else:
        axis.text(0.5, 0.5, "No monthly return data", ha="center", va="center")
        axis.set_xticks([])
    axis.set_title("{} Monthly Returns".format(scenario_name))
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = charts_dir / "monthly_returns_{}.png".format(_safe_name(scenario_name))
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def _trade_pnl_chart(result, charts_dir, plt):
    figure, axis = plt.subplots(figsize=(10, 4))
    plotted = False
    for name, scenario in result.get("scenarios", {}).items():
        values = [
            float(trade.get("pnl", 0.0))
            for trade in scenario.get("trades", [])
            if trade.get("side") == "SELL" and trade.get("status") != "UNFILLED"
        ]
        if values:
            axis.hist(values, bins=min(20, max(5, len(values))), alpha=0.55, label=name)
            plotted = True
    axis.axvline(0, color="#333333", linewidth=0.8)
    axis.set_title("Realized Trade PnL Distribution")
    axis.set_xlabel("PnL per Trade")
    axis.set_ylabel("Count")
    if plotted:
        axis.legend()
    else:
        axis.text(0.5, 0.5, "No realized trades", ha="center", va="center")
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = charts_dir / "trade_pnl_distribution.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def _series(values: Dict[str, Any]):
    return [(str(day), float(value)) for day, value in sorted(values.items()) if value is not None]


def _drawdowns(values: Iterable[float]):
    peak = None
    result = []
    for value in values:
        peak = value if peak is None else max(peak, value)
        result.append((value / peak - 1.0) if peak else 0.0)
    return result


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in str(value))
