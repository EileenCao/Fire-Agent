from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtesting import BacktestEngine


def _spec():
    return StrategySpec.from_dict(
        {
            "strategy_id": "no-leak",
            "version": "1.0.0",
            "name": "无未来数据",
            "universe": ["600000"],
            "frequency": "1d",
            "entry": {"rules": [{"type": "state", "left": "close", "right": 1}]},
            "exit": {"rules": []},
            "position_sizing": {"type": "all_in", "lot_size": 100},
            "initial_capital": 10000,
        }
    )


def test_future_bar_cannot_change_an_earlier_signal():
    prefix = [
        {"date": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1},
        {"date": "2026-01-02", "open": 2, "high": 2, "low": 2, "close": 2},
        {"date": "2026-01-03", "open": 3, "high": 3, "low": 3, "close": 3},
    ]
    first = BacktestEngine().run(_spec(), {"600000": prefix})
    extended = BacktestEngine().run(
        _spec(),
        {
            "600000": prefix
            + [{"date": "2026-01-04", "open": 4, "high": 4, "low": 4, "close": 4}],
        },
    )

    first_trade = first["scenarios"]["default"]["trades"][0]
    extended_trade = extended["scenarios"]["default"]["trades"][0]
    assert first_trade["signal_date"] == extended_trade["signal_date"] == "2026-01-02"
    assert first_trade["date"] == extended_trade["date"] == "2026-01-03"
