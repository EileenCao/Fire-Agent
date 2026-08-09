from mcp_server.domain.strategy import StrategySpec
from mcp_server.storage import SQLiteStore


def _spec():
    return StrategySpec.from_dict(
        {
            "strategy_id": "ma-trend",
            "version": "1.0.0",
            "name": "均线趋势",
            "universe": ["512890"],
            "frequency": "1d",
            "entry": {"rules": [{"type": "state", "left": "close", "right": 1}]},
            "exit": {"rules": [{"type": "state", "left": "close", "right": 1}]},
            "position_sizing": {"type": "all_in"},
            "data_policy": {"source_version": "a-stock-data:3.6.0"},
        }
    )


def test_strategy_version_round_trip_and_activation(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    spec = _spec()

    saved = store.save_strategy_version(spec, status="approved")
    assert saved["strategy_id"] == "ma-trend"
    assert saved["status"] == "approved"

    store.activate_strategy("ma-trend", "1.0.0")
    active = store.get_active_strategy()

    assert active is not None
    assert active.strategy_id == "ma-trend"
    assert active.version == "1.0.0"
    assert active.to_dict()["universe"] == ["512890"]


def test_strategy_versions_keep_multiple_versions(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    first = _spec()
    second = StrategySpec.from_dict(dict(first.to_dict(), version="2.0.0"))

    store.save_strategy_version(first)
    store.save_strategy_version(second)

    assert [item["version"] for item in store.list_strategy_versions()] == ["1.0.0", "2.0.0"]
