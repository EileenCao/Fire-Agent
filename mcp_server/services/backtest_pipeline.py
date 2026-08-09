"""Shared deterministic enrichment used by CLI and MCP backtest runs."""

from typing import Any, Dict, Iterable, List, Optional

from mcp_server.domain.identifiers import normalize_ticker
from mcp_server.services.performance import calculate_benchmark_metrics


def enrich_backtest_result(
    result: Dict[str, Any],
    spec,
    benchmark_data: Optional[Dict[str, Iterable[Dict[str, Any]]]] = None,
    benchmark_provenance: Optional[Dict[str, Any]] = None,
    benchmark_errors: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Attach run assumptions and benchmark comparisons without changing facts.

    The strategy result remains the source of truth. A failed or poorly aligned
    benchmark only changes the relative-performance section to ``unavailable``.
    """

    result["assumptions"] = {
        "benchmark": dict(spec.benchmark) if spec.benchmark else None,
        "risk_free_rate_annual": spec.risk_free_rate_annual,
    }
    selected = dict(spec.benchmark) if spec.benchmark else None
    if selected is None:
        result["benchmark"] = {"selected": None, "status": "not_selected"}
        result["benchmark_comparison"] = {
            "status": "not_selected",
            "reason": "本次明确选择不使用基准",
        }
        result["benchmark_comparisons"] = {}
        return result

    code, market = normalize_ticker(str(selected.get("code")), selected.get("market"))
    bars = _find_bars(benchmark_data or {}, code, market)
    errors = benchmark_errors or {}
    if not bars:
        reason = errors.get(code) or errors.get(str(selected.get("code"))) or "基准数据缺失"
        unavailable = {
            "status": "unavailable",
            "reason": reason,
            "coverage": 0.0,
        }
        result["benchmark"] = {
            "selected": selected,
            "status": "unavailable",
            "reason": reason,
            "provenance": dict(benchmark_provenance or {}),
        }
        result["benchmark_comparison"] = unavailable
        result["benchmark_comparisons"] = {
            name: dict(unavailable) for name in result.get("scenarios", {})
        }
        return result

    curve = _equity_curve(bars)
    comparisons = {}
    for name, scenario in result.get("scenarios", {}).items():
        comparisons[name] = calculate_benchmark_metrics(
            scenario.get("equity_curve", {}),
            curve,
            risk_free_rate_annual=spec.risk_free_rate_annual,
        )
    first = next(iter(comparisons.values()), {"status": "unavailable", "reason": "没有可比较情景"})
    result["benchmark"] = {
        "selected": selected,
        "status": "ok",
        "data_start": min(curve) if curve else None,
        "data_end": max(curve) if curve else None,
        "provenance": dict(benchmark_provenance or {}),
    }
    result["benchmark_equity_curve"] = curve
    result["benchmark_comparison"] = first
    result["benchmark_comparisons"] = comparisons
    return result


def benchmark_provider_code(benchmark: Optional[Dict[str, Any]]) -> Optional[str]:
    if not benchmark:
        return None
    code, market = normalize_ticker(str(benchmark["code"]), benchmark.get("market"))
    return market + code


def _find_bars(
    data: Dict[str, Iterable[Dict[str, Any]]], code: str, market: str
) -> List[Dict[str, Any]]:
    candidates = (code, market + code, code + "." + market)
    for candidate in candidates:
        if candidate in data:
            return sorted(
                [dict(bar) for bar in data[candidate] if isinstance(bar, dict)],
                key=lambda bar: str(bar.get("date", "")),
            )
    return []


def _equity_curve(bars: List[Dict[str, Any]]) -> Dict[str, float]:
    values = []
    for bar in bars:
        day = str(bar.get("date", ""))[:10]
        close = bar.get("adj_close", bar.get("close"))
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        if day and close > 0:
            values.append((day, close))
    if not values:
        return {}
    first = values[0][1]
    return {day: round(close / first, 12) for day, close in values}
