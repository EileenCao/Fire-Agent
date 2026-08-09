"""Deterministic indicator calculations used by structured strategies."""

from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence


def wilder_rsi(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Return a Wilder-smoothed RSI series aligned to ``values``."""

    if period <= 0:
        raise ValueError("RSI period must be positive")
    if len(values) < period + 1:
        return [None] * len(values)

    prices = [float(value) for value in values]
    result: List[Optional[float]] = [None] * len(prices)
    changes = [prices[index] - prices[index - 1] for index in range(1, len(prices))]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period
    result[period] = _rsi_value(average_gain, average_loss)

    for index in range(period + 1, len(prices)):
        change = changes[index - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period
        result[index] = _rsi_value(average_gain, average_loss)
    return result


def build_indicator_series(spec, bars: Sequence[Mapping[str, Any]]) -> Dict[str, List[Optional[float]]]:
    """Build daily-aligned series for the RSI definitions in a strategy."""

    normalized_bars = list(bars)
    closes = [float(bar["close"]) for bar in normalized_bars]
    result: Dict[str, List[Optional[float]]] = {}
    for definition in getattr(spec, "indicators", []) or []:
        indicator_id = str(definition["id"])
        if definition.get("type") != "rsi":
            raise ValueError("unsupported indicator type: {}".format(definition.get("type")))
        period = int(definition["period"])
        if definition.get("timeframe") == "1d":
            result[indicator_id] = wilder_rsi(closes, period)
        elif definition.get("timeframe") == "1w":
            result[indicator_id] = _completed_weekly_rsi(normalized_bars, period)
        else:
            raise ValueError(
                "unsupported RSI timeframe: {}".format(definition.get("timeframe"))
            )
    return result


def _completed_weekly_rsi(
    bars: Sequence[Mapping[str, Any]], period: int
) -> List[Optional[float]]:
    weeks: List[Dict[str, Any]] = []
    positions: Dict[tuple, int] = {}
    for index, bar in enumerate(bars):
        day = _as_date(bar["date"])
        key = day.isocalendar()[:2]
        week_index = positions.get(key)
        if week_index is None:
            week_index = len(weeks)
            positions[key] = week_index
            weeks.append({"indices": [], "close": None})
        weeks[week_index]["indices"].append(index)
        weeks[week_index]["close"] = float(bar["close"])

    weekly_values = [week["close"] for week in weeks]
    weekly_rsi = wilder_rsi(weekly_values, period)
    result: List[Optional[float]] = [None] * len(bars)
    for week_index, week in enumerate(weeks):
        previous = weekly_rsi[week_index - 1] if week_index > 0 else None
        current = weekly_rsi[week_index]
        indices = week["indices"]
        for index in indices[:-1]:
            result[index] = previous
        if indices:
            result[indices[-1]] = current
    return result


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 50.0 if average_gain == 0 else 100.0
    if average_gain == 0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
