"""Pure sizing calculations for layered core/tactical RSI strategies."""

from typing import Any, Dict, Iterable, List, Optional, Set


def build_ladder_state(spec, bars: Iterable[Dict[str, Any]], index: int) -> Dict[str, Any]:
    """Build historical drawdown and MA250 state for one daily bar."""

    bars = list(bars)
    ladder = _ladder_config(spec)
    closes = [float(bar["close"]) for bar in bars]
    current_close = closes[index]
    anchor_window = int(ladder.get("anchor_window", 120))
    history = closes[max(0, index - anchor_window) : index]
    anchor_price = max(history) if history else None
    is_new_anchor_high = bool(anchor_price is not None and current_close > anchor_price)
    drawdown_pct = (
        max(0.0, 1.0 - current_close / anchor_price)
        if anchor_price and anchor_price > 0
        else None
    )

    thresholds = [float(value) for value in ladder.get("thresholds", [])]
    base_level = 0
    if drawdown_pct is not None:
        for level, threshold in enumerate(thresholds, start=1):
            if drawdown_pct + 1e-12 >= threshold:
                base_level = level

    annual_period = int(ladder.get("annual_period", 250))
    ma250 = None
    if index + 1 >= annual_period:
        ma250 = sum(closes[index - annual_period + 1 : index + 1]) / annual_period
    ma250_gap_pct = (
        max(0.0, 1.0 - current_close / ma250) if ma250 and ma250 > 0 else None
    )
    annual_boost = 0
    if ma250_gap_pct is not None:
        deep_threshold = float(ladder.get("annual_deep_threshold", 0.05))
        boost_threshold = float(ladder.get("annual_boost_threshold", 0.0))
        if ma250_gap_pct >= deep_threshold:
            annual_boost = 2
        elif ma250_gap_pct > boost_threshold:
            annual_boost = 1

    max_level = int(ladder.get("max_level", len(thresholds)))
    final_level = min(max_level, base_level + annual_boost)
    amounts = [float(value) for value in ladder.get("amounts", [])]
    ladder_amount = amounts[final_level - 1] if final_level and amounts else 0.0
    return {
        "data_as_of": str(bars[index]["date"]),
        "current_close": current_close,
        "anchor_price": anchor_price,
        "history_count": len(history),
        "is_new_anchor_high": is_new_anchor_high,
        "drawdown_pct": drawdown_pct,
        "base_level": base_level,
        "ma250": ma250,
        "ma250_gap_pct": ma250_gap_pct,
        "annual_boost": annual_boost,
        "final_level": final_level,
        "ladder_amount": ladder_amount,
    }


def build_fibonacci_state(
    spec, bars: Iterable[Dict[str, Any]], index: int
) -> Dict[str, Any]:
    """Build prior-window Fibonacci levels and first close-crossings."""

    bars = list(bars)
    fibonacci = _fibonacci_config(spec)
    current_close = float(bars[index]["close"])
    anchor_window = int(fibonacci.get("anchor_window", 120))
    history = bars[max(0, index - anchor_window) : index]
    base = {
        "data_as_of": str(bars[index]["date"]),
        "current_close": current_close,
        "previous_close": (
            float(bars[index - 1]["close"]) if index > 0 else None
        ),
        "anchor_window": anchor_window,
        "history_count": len(history),
        "anchor_high": None,
        "anchor_low": None,
        "level_prices": [],
        "crossed_levels": [],
        "final_level": 0,
        "ladder_amount": 0.0,
    }
    if len(history) < anchor_window:
        return base

    anchor_high = max(float(bar["high"]) for bar in history)
    anchor_low = min(float(bar["low"]) for bar in history)
    ratios = [float(value) for value in fibonacci.get("ratios", [])]
    level_prices = [
        anchor_high - ratio * (anchor_high - anchor_low) for ratio in ratios
    ]
    previous_close = base["previous_close"]
    crossed_levels = []
    if previous_close is not None:
        crossed_levels = [
            level
            for level, price in enumerate(level_prices, start=1)
            if previous_close > price and current_close <= price
        ]
    final_level = max(crossed_levels, default=0)
    amounts = [float(value) for value in fibonacci.get("amounts", [])]
    ladder_amount = amounts[final_level - 1] if final_level and crossed_levels else 0.0
    base.update(
        {
            "anchor_high": anchor_high,
            "anchor_low": anchor_low,
            "level_prices": level_prices,
            "crossed_levels": crossed_levels,
            "final_level": final_level,
            "ladder_amount": ladder_amount,
        }
    )
    return base


def resolve_fibonacci_level(spec, state: Dict[str, Any]) -> Dict[str, Any]:
    """Return the deepest newly crossed Fibonacci level for one signal bar."""

    fibonacci = _fibonacci_config(spec)
    final_level = int(state.get("final_level", 0))
    new_levels = [int(level) for level in state.get("crossed_levels", [])]
    amounts = [float(value) for value in fibonacci.get("amounts", [])]
    ladder_amount = amounts[final_level - 1] if final_level and new_levels else 0.0
    return {
        "level": final_level,
        "new_levels": new_levels,
        "ladder_amount": ladder_amount,
    }


def resolve_ladder_level(
    spec,
    state: Dict[str, Any],
    triggered_levels: Set[int],
    consume: bool = True,
) -> Dict[str, Any]:
    """Consume newly reached ladder levels once per anchor cycle."""

    ladder = _ladder_config(spec)
    final_level = int(state.get("final_level", 0))
    prior = set() if state.get("is_new_anchor_high") else set(triggered_levels)
    if not consume:
        return {
            "level": final_level,
            "new_levels": [],
            "triggered_levels": sorted(prior),
            "ladder_amount": 0.0,
        }
    new_levels = [level for level in range(1, final_level + 1) if level not in prior]
    updated = prior | set(new_levels)
    amounts = [float(value) for value in ladder.get("amounts", [])]
    ladder_amount = amounts[final_level - 1] if new_levels and final_level else 0.0
    return {
        "level": final_level,
        "new_levels": new_levels,
        "triggered_levels": sorted(updated),
        "ladder_amount": ladder_amount,
    }


def resolve_tactical_cash(rsi_cash: float, ladder_amount: float) -> float:
    """Use the larger RSI or ladder amount, without double counting."""

    return max(float(rsi_cash), float(ladder_amount))


def resolve_recovery_levels(
    state: Dict[str, Any], tracker: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Return ladder levels to trim as the base drawdown level recovers.

    The tracker covers one anchor cycle. A new anchor high clears it, and
    annual MA boosts are deliberately excluded from the recovery trigger so a
    disappearing boost cannot create a false recovery sell.
    """

    previous = dict(tracker or {})
    is_new_anchor_high = bool(state.get("is_new_anchor_high"))
    if is_new_anchor_high:
        previous_highest = 0
        sold_levels = set()
    else:
        previous_highest = max(0, int(previous.get("highest_base_level", 0)))
        sold_levels = {int(level) for level in previous.get("sold_levels", [])}

    current_level = max(0, int(state.get("base_level", 0)))
    sell_levels = []
    if not is_new_anchor_high:
        sell_levels = [
            level
            for level in range(previous_highest, 0, -1)
            if current_level < level and level not in sold_levels
        ]
        sold_levels.update(sell_levels)

    highest_base_level = max(previous_highest, current_level)
    updated_tracker = {
        "highest_base_level": highest_base_level,
        "sold_levels": sorted(sold_levels),
    }
    return {
        "current_base_level": current_level,
        "previous_highest_base_level": previous_highest,
        "highest_base_level": highest_base_level,
        "sell_levels": sell_levels,
        "tracker": updated_tracker,
        "is_new_anchor_high": is_new_anchor_high,
    }


def _ladder_config(spec) -> Dict[str, Any]:
    if hasattr(spec, "position_sizing"):
        sizing = spec.position_sizing or {}
    else:
        sizing = spec.get("position_sizing") or {}
    return dict(sizing.get("drawdown_ladder") or {})


def _fibonacci_config(spec) -> Dict[str, Any]:
    if hasattr(spec, "position_sizing"):
        sizing = spec.position_sizing or {}
    else:
        sizing = spec.get("position_sizing") or {}
    return dict(sizing.get("fibonacci_ladder") or {})
