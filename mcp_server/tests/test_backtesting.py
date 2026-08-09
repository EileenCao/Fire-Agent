from mcp_server.services.backtesting import BacktestEngine
from mcp_server.domain.strategy import StrategySpec


def _strategy(**overrides):
    payload = {
        "strategy_id": "ma-trend",
        "version": "1.0.0",
        "name": "均线趋势",
        "universe": ["600000"],
        "frequency": "1d",
        "entry": {
            "rules": [
                {"type": "cross_above", "left": "sma_2", "right": "sma_3"}
            ]
        },
        "exit": {
            "rules": [
                {"type": "cross_below", "left": "sma_2", "right": "sma_3"}
            ]
        },
        "position_sizing": {"type": "all_in"},
        "initial_capital": 1000.0,
        "cost_profile": {"commission_rate": 0.0, "slippage_rate": 0.0},
    }
    payload.update(overrides)
    return StrategySpec.from_dict(payload)


def test_strategy_spec_rejects_formal_run_without_position_sizing():
    payload = _strategy().to_dict()
    payload.pop("position_sizing")

    spec = StrategySpec.from_dict(payload)

    assert any("仓位" in error for error in spec.validation_errors)
    assert spec.is_valid is False


def test_signal_at_close_executes_at_next_trading_day_open():
    spec = _strategy()
    bars = [
        {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-04", "open": 20, "high": 21, "low": 19, "close": 20},
        {"date": "2026-01-05", "open": 20, "high": 20, "low": 18, "close": 18},
    ]

    result = BacktestEngine().run(spec, {"600000": bars})

    trade = result["scenarios"]["default"]["trades"][0]
    assert trade["side"] == "BUY"
    assert trade["signal_date"] == "2026-01-04"
    assert trade["date"] == "2026-01-05"
    assert trade["price"] == 20


def test_stop_and_take_same_day_produce_two_scenarios_and_provenance():
    spec = _strategy(
        stop_loss_pct=0.05,
        take_profit_pct=0.05,
        data_policy={"source_name": "fixture", "source_version": "a-stock-data:3.6.0"},
    )
    bars = [
        {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-04", "open": 10, "high": 12, "low": 10, "close": 12},
        {"date": "2026-01-05", "open": 10, "high": 12, "low": 8, "close": 10},
    ]

    result = BacktestEngine().run(spec, {"600000": bars})

    assert set(result["scenarios"]) == {"stop_first", "take_first"}
    assert result["provenance"]["source_version"] == "a-stock-data:3.6.0"
    assert result["scenarios"]["stop_first"]["warnings"]
