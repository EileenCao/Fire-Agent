from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtesting import BacktestEngine


def test_prefixed_a_share_code_is_normalized_for_strategy_and_backtest():
    spec = StrategySpec.from_dict(
        {
            "strategy_id": "identifier",
            "version": "1.0.0",
            "name": "代码归一化",
            "universe": ["SH512890"],
            "frequency": "1d",
            "entry": {"rules": []},
            "exit": {"rules": []},
            "position_sizing": {"type": "all_in"},
        }
    )
    data = {
        "SH512890": [
            {"date": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1}
        ]
    }

    assert spec.universe == ["512890"]
    assert BacktestEngine().run(spec, data)["strategy_id"] == "identifier"
