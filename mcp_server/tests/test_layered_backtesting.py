from copy import deepcopy

from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtesting import BacktestEngine

from mcp_server.tests.test_rsi_backtesting import (
    _bars,
    _close_rsi_spec,
    _full_exit_spec,
)


def _layered_spec(base=None, ratio=0.5, with_ladder=True):
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
    return StrategySpec.from_dict(payload)


def _scenario(spec, closes):
    result = BacktestEngine().run(spec, {"512890": _bars(closes)})
    return result["scenarios"]["default"]


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

