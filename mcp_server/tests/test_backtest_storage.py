from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtesting import BacktestEngine
from mcp_server.storage import SQLiteStore


def _spec(version="1.0.0"):
    return StrategySpec.from_dict(
        {
            "strategy_id": "ma-trend",
            "version": version,
            "name": "均线趋势",
            "universe": ["600000"],
            "frequency": "1d",
            "entry": {"rules": [{"type": "cross_above", "left": "sma_2", "right": "sma_3"}]},
            "exit": {"rules": [{"type": "cross_below", "left": "sma_2", "right": "sma_3"}]},
            "position_sizing": {"type": "all_in"},
            "data_policy": {"source_version": "a-stock-data:3.6.0"},
        }
    )


def _bars():
    return [
        {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-04", "open": 10, "high": 12, "low": 10, "close": 12},
        {"date": "2026-01-05", "open": 20, "high": 20, "low": 18, "close": 18},
    ]


def test_backtest_run_and_signal_evidence_round_trip(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    spec = _spec()
    result = BacktestEngine().run(spec, {"600000": _bars()})

    run = store.save_backtest_run(spec, result)
    loaded = store.get_backtest_result(run["id"])
    evidence = store.list_signal_evidence(run["id"])

    assert loaded["strategy_version"] == "1.0.0"
    assert loaded["result"]["provenance"]["source_version"] == "a-stock-data:3.6.0"
    assert evidence
    assert evidence[0]["source_version"] == "a-stock-data:3.6.0"
