from mcp_server.domain.strategy import StrategySpec
from mcp_server.storage import SQLiteStore


def _payload():
    return {
        "strategy_id": "lifecycle",
        "version": "1.0.0",
        "name": "生命周期",
        "universe": ["600000"],
        "frequency": "1d",
        "entry": {"rules": []},
        "exit": {"rules": []},
        "position_sizing": {"type": "all_in"},
    }


def test_only_valid_approved_strategy_can_be_activated(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    invalid = StrategySpec.from_dict({"strategy_id": "bad", "version": "1", "name": "bad"})
    valid = StrategySpec.from_dict(_payload())

    store.save_strategy_version(invalid, status="draft")
    store.save_strategy_version(valid, status="approved")

    try:
        store.activate_strategy("bad", "1")
        assert False, "invalid strategy should not activate"
    except ValueError:
        pass
    store.activate_strategy("lifecycle", "1.0.0")
    assert store.get_active_strategy().strategy_id == "lifecycle"
