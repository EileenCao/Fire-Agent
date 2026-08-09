from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.observer import StrategyObserver


def test_observer_returns_rule_signal_and_evidence_for_next_session():
    spec = StrategySpec.from_dict(
        {
            "strategy_id": "observer",
            "version": "1.0.0",
            "name": "日观察",
            "universe": ["512890"],
            "frequency": "1d",
            "entry": {"rules": [{"type": "state", "left": "close", "right": 1}]},
            "exit": {"rules": []},
            "position_sizing": {"type": "all_in"},
            "data_policy": {
                "source_name": "fixture",
                "source_url": "https://example.invalid/data",
                "source_version": "a-stock-data:3.6.0",
                "skill_name": "a-stock-data",
                "skill_version": "3.6.0",
            },
        }
    )
    data = {
        "512890": [
            {"date": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1},
            {"date": "2026-01-02", "open": 2, "high": 2, "low": 2, "close": 2},
        ]
    }

    result = StrategyObserver().observe(spec, data, positions={})
    signal = result["signals"][0]

    assert signal["action"] == "BUY"
    assert signal["execution"] == "next_trading_day_open"
    assert signal["evidence"]["signal_date"] == "2026-01-02"
    assert signal["evidence"]["source_version"] == "a-stock-data:3.6.0"
    assert result["ai_observation"]["status"] == "not_generated"
