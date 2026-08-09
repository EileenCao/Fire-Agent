from mcp_server.services.artifacts import write_backtest_artifacts

from mcp_server.tests.test_layered_backtesting import _layered_spec, _scenario


def test_layered_report_shows_holdings_ladder_and_skipped_sells(tmp_path):
    scenario = _scenario(_layered_spec(ratio=0.5), [10, 9, 8, 7, 6, 5, 4, 3])
    result = {
        "strategy_id": "layered-fixture",
        "strategy_version": "1.0.0",
        "provenance": {
            "layered": {
                "core": {"ratio": 0.5, "trigger": "first_entry_signal", "hold": True},
                "drawdown_ladder": {
                    "anchor_window": 120,
                    "thresholds": [0.1, 0.2, 0.3],
                    "amounts": [500, 1000, 1500],
                    "annual_period": 250,
                    "annual_boost_threshold": 0.0,
                    "annual_deep_threshold": 0.05,
                },
            }
        },
        "scenarios": {"default": dict(scenario)},
        "validation": {},
    }
    result["scenarios"]["default"]["metrics"].update(
        {
            "core_position_quantity": 6200,
            "tactical_position_quantity": 300,
            "core_market_value": 24800,
            "tactical_market_value": 1200,
            "layered_cash": 24000,
            "skipped_sell_signal_count": 2,
        }
    )
    result["scenarios"]["default"]["skipped_sell_signals"] = [{}, {}]

    paths = write_backtest_artifacts(tmp_path, result, run_id=1, created_at="2026-08-09T00:00:00+08:00")
    report = open(paths["report"], encoding="utf-8").read()

    assert "Layered holdings" in report
    assert "Ladder assumptions" in report
    assert "Skipped sell signals: 2" in report
