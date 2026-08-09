from mcp_server.services.observer import StrategyObserver

from mcp_server.tests.test_layered_backtesting import _bars, _layered_spec, _scenario


def test_observer_exposes_layered_holdings_and_ladder_evidence():
    spec = _layered_spec(ratio=0.5)
    scenario = _scenario(spec, [10, 9, 8, 7, 6, 5, 4, 3])

    observed = StrategyObserver().observe(
        spec,
        {"512890": _bars([10, 9, 8, 7, 6, 5, 4, 3])},
        positions={"512890": scenario["positions"]["512890"]},
    )
    signal = observed["signals"][0]

    assert signal["core_quantity"] == scenario["positions"]["512890"]["core"]["quantity"]
    assert signal["tactical_quantity"] == scenario["positions"]["512890"]["tactical"]["quantity"]
    assert signal["ladder_state"]["final_level"] == 3
    assert signal["signal_evidence"]["ladder"]["ladder_amount"] == 1500


def test_observer_sell_evidence_identifies_tactical_only_position():
    from mcp_server.tests.test_rsi_backtesting import _full_exit_spec

    spec = _layered_spec(base=_full_exit_spec(), ratio=0.3, with_ladder=False)
    observed = StrategyObserver().observe(
        spec,
        {"512890": _bars([4, 3, 2, 1, 2, 3, 4, 5])},
        positions={
            "512890": {
                "core": {"quantity": 6200},
                "tactical": {"quantity": 100},
            }
        },
    )
    signal = observed["signals"][0]

    assert signal["action"] == "SELL"
    assert signal["tactical_quantity"] == 100
    assert signal["signal_evidence"]["book"] == "tactical"
    assert signal["signal_evidence"]["sell_quantity"] == 100
