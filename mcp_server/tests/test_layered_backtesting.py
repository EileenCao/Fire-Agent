from copy import deepcopy

from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtesting import BacktestEngine

from mcp_server.tests.test_rsi_backtesting import (
    _bars,
    _close_rsi_spec,
    _full_exit_spec,
)


def _layered_spec(
    base=None, ratio=0.5, with_ladder=True, exit_mode=None, sell_basis=None
):
    payload = (base or _close_rsi_spec()).to_dict()
    payload["position_sizing"]["core"] = {
        "ratio": ratio,
        "trigger": "first_entry_signal",
        "hold": True,
    }
    if with_ladder:
        payload["position_sizing"]["drawdown_ladder"] = {
            "anchor_window": 120,
            "thresholds": [0.10, 0.20, 0.30],
            "amounts": [500, 1000, 1500],
            "annual_period": 250,
            "annual_boost_threshold": 0.0,
            "annual_deep_threshold": 0.05,
            "max_level": 3,
            "combine": "max",
            "requires_rsi_entry": True,
            "reset": "new_anchor_high",
        }
    if exit_mode is not None:
        payload["position_sizing"]["exit_mode"] = exit_mode
    if sell_basis is not None:
        payload["position_sizing"]["sell_basis"] = sell_basis
    return StrategySpec.from_dict(payload)


def _scenario(spec, closes):
    result = BacktestEngine().run(spec, {"512890": _bars(closes)})
    return result["scenarios"]["default"]


def _fibonacci_spec(ratio=0.0):
    payload = _close_rsi_spec().to_dict()
    payload["position_sizing"]["core"] = {
        "ratio": ratio,
        "trigger": "first_entry_signal",
        "hold": True,
    }
    payload["position_sizing"]["fibonacci_ladder"] = {
        "anchor_window": 120,
        "ratios": [0.382, 0.618, 0.786],
        "amounts": [500, 2000, 3000],
        "requires_rsi_entry": True,
        "crossing": "first_close_below",
    }
    return StrategySpec.from_dict(payload)


def test_first_rsi_entry_buys_core_once_and_keeps_tactical_book_separate():
    scenario = _scenario(_layered_spec(ratio=0.5), [10, 9, 8, 7, 6, 5, 4, 3])

    core_buys = [trade for trade in scenario["trades"] if trade["source"] == "CORE_BUY"]
    tactical_buys = [
        trade for trade in scenario["trades"] if trade["source"] == "SIGNAL_BUY"
    ]
    positions = scenario["positions"]["512890"]

    assert len(core_buys) == 1
    assert core_buys[0]["quantity"] == 6200
    assert tactical_buys[0]["quantity"] == 300
    assert positions["core"]["quantity"] == 6200
    assert positions["tactical"]["quantity"] > 0


def test_zero_core_ratio_runs_tactical_only_without_core_buy():
    scenario = _scenario(_layered_spec(ratio=0.0), [10, 9, 8, 7, 6, 5, 4, 3])

    assert any(trade["source"] == "SIGNAL_BUY" for trade in scenario["trades"])
    assert not any(trade["source"] == "CORE_BUY" for trade in scenario["trades"])
    assert scenario["positions"]["512890"]["core"]["quantity"] == 0


def test_layered_close_policy_reports_exact_suspension_warning():
    bars = _bars([10, 9, 8, 7, 6, 5, 4, 3])
    bars[-1]["suspended"] = True

    result = BacktestEngine().run(_layered_spec(ratio=0.5), {"512890": bars})
    warnings = result["scenarios"]["default"]["warnings"]

    assert "512890 2026-01-08 停牌，无法产生或执行交易" in warnings


def test_full_tactical_exit_does_not_sell_core_lots():
    base = _full_exit_spec()
    scenario = _scenario(_layered_spec(base=base, ratio=0.3, with_ladder=False), [4, 3, 2, 1, 2, 3, 4, 5])

    core_quantity = scenario["positions"]["512890"]["core"]["quantity"]
    sells = [trade for trade in scenario["trades"] if trade["side"] == "SELL"]

    assert core_quantity > 0
    assert sells
    assert all(trade["book"] == "tactical" for trade in sells)
    assert scenario["positions"]["512890"]["core"]["quantity"] == core_quantity


def test_ladder_amount_uses_maximum_without_double_counting_rsi_amount():
    scenario = _scenario(_layered_spec(ratio=0.3), [10, 9, 8, 7, 6, 5, 4, 3])

    tactical_buy = next(
        trade for trade in scenario["trades"] if trade["source"] == "SIGNAL_BUY"
    )

    assert tactical_buy["quantity"] == 300
    assert tactical_buy["signal_evidence"]["ladder"]["ladder_amount"] == 1500
    assert tactical_buy["signal_evidence"]["buy_cash"] == 1500


def test_ladder_cannot_create_buy_without_rsi_entry_signal():
    payload = deepcopy(_close_rsi_spec().to_dict())
    payload["entry"]["conditions"][0]["value"] = -1
    spec = _layered_spec(base=StrategySpec.from_dict(payload), ratio=0.5)
    scenario = _scenario(spec, [10, 9, 8, 7, 6, 5, 4, 3])

    assert scenario["trades"] == []
    assert scenario["positions"]["512890"]["core"]["quantity"] == 0


def test_fibonacci_ladder_uses_deepest_new_crossing_amount_once_per_level():
    closes = [1.05] * 120 + [1.02, 0.97, 0.94, 0.93]
    bars = _bars(closes)
    for bar in bars:
        bar["high"] = 1.10
        bar["low"] = 0.90

    result = BacktestEngine().run(_fibonacci_spec(), {"512890": bars})
    scenario = result["scenarios"]["default"]
    buys = [trade for trade in scenario["trades"] if trade["source"] == "SIGNAL_BUY"]

    assert [trade["signal_evidence"]["fibonacci"]["new_levels"] for trade in buys[:3]] == [
        [1],
        [2],
        [3],
    ]
    assert [trade["signal_evidence"]["fibonacci"]["ladder_amount"] for trade in buys[:3]] == [
        500,
        2000,
        3000,
    ]
    assert buys[3]["signal_evidence"]["fibonacci"]["new_levels"] == []
    assert [trade["signal_evidence"]["buy_cash"] for trade in buys] == [
        500,
        2000,
        3000,
        500,
    ]


def test_recovery_exit_sells_ladder_tranches_from_tactical_book_only():
    spec = _layered_spec(
        base=_full_exit_spec(), ratio=0.5, exit_mode="recovery"
    )
    scenario = _scenario(
        spec, [1.0] * 120 + [0.9, 0.8, 0.7, 0.8, 0.9, 1.0, 1.1]
    )

    recovery_sells = [
        trade for trade in scenario["trades"] if trade["source"] == "RECOVERY_SELL"
    ]

    assert [
        trade["signal_evidence"]["recovery"]["level"]
        for trade in recovery_sells
    ] == [3, 2, 1]
    assert all(trade["book"] == "tactical" for trade in recovery_sells)
    assert not [
        trade
        for trade in scenario["trades"]
        if trade["side"] == "SELL" and trade["book"] == "core"
    ]


def test_rsi_exit_mode_keeps_original_sell_source():
    spec = _layered_spec(base=_full_exit_spec(), ratio=0.5, exit_mode="rsi")
    scenario = _scenario(
        spec, [1.0] * 120 + [0.9, 0.8, 0.7, 0.8, 0.9, 1.0, 1.1]
    )

    assert any(
        trade["source"] == "SELL" and trade["reason"] == "EXIT_RULE"
        for trade in scenario["trades"]
    )
    assert not any(trade["source"] == "RECOVERY_SELL" for trade in scenario["trades"])


def test_profitable_sell_basis_includes_older_profitable_tactical_batches_only():
    spec = _layered_spec(
        base=_full_exit_spec(),
        ratio=0.5,
        with_ladder=False,
        sell_basis="profitable_tactical",
    )
    scenario = _scenario(spec, [1.0, 0.9, 0.8, 0.82, 0.84, 0.86, 0.88, 0.90])

    buys = [trade for trade in scenario["trades"] if trade["source"] == "SIGNAL_BUY"]
    sells = [trade for trade in scenario["trades"] if trade["source"] == "SELL"]

    assert len(buys) == 2
    assert len(sells) == 1
    assert sells[0]["quantity"] == 1200
    assert sells[0]["requested_quantity"] == 1200
    assert set(sells[0]["lot_ids"]) == {
        buys[0]["lot_id"],
        buys[1]["lot_id"],
    }
    assert sells[0]["signal_evidence"]["profit_tactical_quantity"] == 1200
