from datetime import date, timedelta

import pytest

from mcp_server.services.layered_sizing import (
    build_ladder_state,
    resolve_ladder_level,
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
