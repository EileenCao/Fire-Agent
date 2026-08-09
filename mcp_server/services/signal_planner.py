"""Deterministic condition-count signal planning for structured strategies."""

from math import floor
from typing import Any, Dict, List, Optional, Sequence

from mcp_server.services.indicators import build_indicator_series


def build_signal_plan(
    spec,
    bars: Sequence[Dict[str, Any]],
    index: int,
    quantity: int,
    indicator_series: Optional[Dict[str, List[Optional[float]]]] = None,
) -> Dict[str, Any]:
    """Return one auditable action plan for a condition-count strategy day."""

    if index < 0 or index >= len(bars):
        raise IndexError("signal index is outside the bar series")
    series = indicator_series or build_indicator_series(spec, bars)
    _, entry_evidence = _evaluate_conditions(
        spec.entry.get("conditions") or [], series, index
    )
    _, exit_evidence = _evaluate_conditions(
        spec.exit.get("conditions") or [], series, index
    )
    entry_count = sum(item["matched"] for item in entry_evidence)
    exit_count = sum(item["matched"] for item in exit_evidence)
    held_quantity = max(0, int(quantity))
    lot_size = _lot_size(spec)

    action = "HOLD"
    buy_cash = 0.0
    sell_quantity = 0
    reason = None
    if exit_count > 0:
        if held_quantity > 0:
            action = "SELL"
            sell_quantity = _sell_quantity(spec, exit_count, held_quantity, lot_size)
            reason = "EXIT_RULE"
        else:
            reason = "EXIT_WITHOUT_POSITION"
    elif entry_count > 0:
        action = "BUY"
        buy_cash = float(
            (spec.entry.get("amount_by_count") or {}).get(str(entry_count), 0.0)
        )
        reason = "ENTRY_RULE"

    evidence = {
        "signal_date": str(bars[index]["date"]),
        "data_as_of": str(bars[index]["date"]),
        "indicator_values": {
            indicator_id: _value_at(values, index)
            for indicator_id, values in series.items()
        },
        "entry_conditions": entry_evidence,
        "exit_conditions": exit_evidence,
        "entry_count": entry_count,
        "exit_count": exit_count,
        "position_quantity": held_quantity,
    }
    if reason:
        evidence["reason"] = reason
    return {
        "action": action,
        "entry_count": entry_count,
        "exit_count": exit_count,
        "buy_cash": buy_cash,
        "sell_quantity": sell_quantity,
        "signal_date": str(bars[index]["date"]),
        "evidence": evidence,
    }


def _evaluate_conditions(conditions, series, index):
    evidence = []
    for condition in conditions:
        value = _value_at(series.get(condition.get("indicator"), []), index)
        matched = value is not None and _compare(
            value, float(condition["value"]), condition.get("operator", ">")
        )
        evidence.append(
            {
                "id": condition.get("id"),
                "indicator": condition.get("indicator"),
                "value": value,
                "operator": condition.get("operator"),
                "threshold": float(condition["value"]),
                "matched": matched,
            }
        )
    return sum(item["matched"] for item in evidence), evidence


def _sell_quantity(spec, exit_count, quantity, lot_size):
    fractions = spec.exit.get("fraction_by_count") or {}
    fraction = float(fractions.get(str(exit_count), 0.0))
    if fraction >= 1.0:
        return quantity
    return floor(quantity * fraction / lot_size) * lot_size


def _lot_size(spec):
    try:
        value = int((spec.position_sizing or {}).get("lot_size", 100))
        return max(1, value)
    except (TypeError, ValueError):
        return 100


def _value_at(values, index):
    return values[index] if index < len(values) else None


def _compare(left, right, operator):
    return {
        "<": left < right,
        "<=": left <= right,
        ">": left > right,
        ">=": left >= right,
        "==": left == right,
    }.get(operator, False)
