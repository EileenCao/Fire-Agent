from datetime import date, timedelta

import pytest

from mcp_server.services.layered_sizing import (
    build_ladder_state,
    build_fibonacci_state,
    resolve_ladder_level,
    resolve_fibonacci_level,
    resolve_recovery_levels,
    resolve_tactical_cash,
)

from mcp_server.tests.test_layered_strategy_contract import _layered_payload


def _bars(closes):
    start = date(2020, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1,
        }
        for index, close in enumerate(closes)
    ]


def _fibonacci_payload():
    payload = _layered_payload()
    payload["position_sizing"].pop("drawdown_ladder")
    payload["position_sizing"]["fibonacci_ladder"] = {
        "anchor_window": 120,
        "ratios": [0.382, 0.618, 0.786],
        "amounts": [500, 2000, 3000],
        "requires_rsi_entry": True,
        "crossing": "first_close_below",
    }
    return payload


def test_drawdown_state_uses_only_prior_120_closes():
    bars = _bars([100.0] * 120 + [89.0])

    state = build_ladder_state(_layered_payload(), bars, 120)

    assert state["anchor_price"] == 100.0
    assert state["drawdown_pct"] == pytest.approx(0.11)
    assert state["base_level"] == 1
    assert state["ma250"] is None


def test_ma250_break_boosts_the_drawdown_level():
    bars = _bars([100.0] * 250 + [94.0])

    state = build_ladder_state(_layered_payload(), bars, 250)

    assert state["ma250"] == pytest.approx(99.976)
    assert state["base_level"] == 0
    assert state["annual_boost"] == 2
    assert state["final_level"] == 2
    assert state["ladder_amount"] == 1000


def test_ladder_levels_are_consumed_once_and_reset_after_new_high():
    bars = _bars([100.0] * 120 + [80.0])
    state = build_ladder_state(_layered_payload(), bars, 120)

    first = resolve_ladder_level(_layered_payload(), state, set())
    repeated = resolve_ladder_level(
        _layered_payload(), state, set(first["triggered_levels"])
    )

    assert first["new_levels"] == [1, 2]
    assert first["ladder_amount"] == 1000
    assert repeated["new_levels"] == []
    assert repeated["ladder_amount"] == 0

    reset_state = build_ladder_state(_layered_payload(), _bars([100.0] * 120 + [101.0]), 120)
    assert reset_state["is_new_anchor_high"] is True


def test_ladder_amount_uses_the_larger_rsi_or_ladder_amount():
    assert resolve_tactical_cash(500, 1000) == 1000
    assert resolve_tactical_cash(1000, 500) == 1000


def test_future_bar_does_not_change_earlier_ladder_state():
    bars = _bars([100.0] * 120 + [89.0] + [100.0] * 10)
    before = build_ladder_state(_layered_payload(), bars, 120)
    bars[125]["close"] = 1000.0

    after = build_ladder_state(_layered_payload(), bars, 120)

    assert after == before


def test_fibonacci_state_uses_prior_high_low_and_first_close_crossings():
    bars = _bars([105.0] * 120 + [102.0, 97.0, 94.0, 93.0])
    for bar in bars[:120]:
        bar["high"] = 110.0
        bar["low"] = 90.0

    first = build_fibonacci_state(_fibonacci_payload(), bars, 120)
    second = build_fibonacci_state(_fibonacci_payload(), bars, 121)
    third = build_fibonacci_state(_fibonacci_payload(), bars, 122)
    repeated = build_fibonacci_state(_fibonacci_payload(), bars, 123)

    assert first["history_count"] == 120
    assert first["anchor_high"] == 110.0
    assert first["anchor_low"] == 90.0
    assert first["level_prices"] == pytest.approx([102.36, 97.64, 94.28])
    assert first["crossed_levels"] == [1]
    assert resolve_fibonacci_level(_fibonacci_payload(), first)["ladder_amount"] == 500
    assert second["crossed_levels"] == [2]
    assert third["crossed_levels"] == [3]
    assert repeated["crossed_levels"] == []
    assert repeated["ladder_amount"] == 0.0


def test_fibonacci_state_requires_a_full_prior_window_and_ignores_future_bars():
    bars = _bars([105.0] * 120 + [102.0, 97.0])
    for bar in bars[:120]:
        bar["high"] = 110.0
        bar["low"] = 90.0

    incomplete = build_fibonacci_state(_fibonacci_payload(), bars, 119)
    before = build_fibonacci_state(_fibonacci_payload(), bars, 120)
    bars[121]["high"] = 10000.0
    after = build_fibonacci_state(_fibonacci_payload(), bars, 120)

    assert incomplete["final_level"] == 0
    assert incomplete["ladder_amount"] == 0.0
    assert after == before


def test_recovery_levels_sell_on_drawdown_recovery_and_reset_at_new_high():
    tracker = {}

    first = resolve_recovery_levels({"base_level": 3}, tracker)
    assert first["sell_levels"] == []
    assert first["highest_base_level"] == 3

    second = resolve_recovery_levels({"base_level": 2}, first["tracker"])
    assert second["sell_levels"] == [3]

    third = resolve_recovery_levels({"base_level": 1}, second["tracker"])
    assert third["sell_levels"] == [2]

    fourth = resolve_recovery_levels({"base_level": 0}, third["tracker"])
    assert fourth["sell_levels"] == [1]

    reset = resolve_recovery_levels(
        {"base_level": 0, "is_new_anchor_high": True}, fourth["tracker"]
    )
    assert reset["sell_levels"] == []
    assert reset["highest_base_level"] == 0


def test_recovery_levels_handle_skipped_levels_without_selling_unreached_levels():
    first = resolve_recovery_levels({"base_level": 3}, {})
    recovered = resolve_recovery_levels({"base_level": 1}, first["tracker"])

    assert recovered["sell_levels"] == [3, 2]

    unreached = resolve_recovery_levels({"base_level": 0}, {})
    assert unreached["sell_levels"] == []
