from mcp_server.server import McpApplication
from mcp_server.storage import SQLiteStore


def _strategy():
    return {
        "strategy_id": "confirmed",
        "version": "1.0.0",
        "name": "正式运行确认测试",
        "universe": ["512890"],
        "frequency": "1d",
        "entry": {"rules": [{"type": "state", "left": "close", "right": 1}]},
        "exit": {"rules": []},
        "position_sizing": {"type": "all_in"},
        "cost_profile": {"template": "realistic", "version": "1.0.0"},
        "data_policy": {"source_name": "fixture", "source_version": "a-stock-data:3.6.0"},
    }


def _data():
    return {
        "512890": [
            {"date": "2026-01-01", "open": 2, "high": 2, "low": 2, "close": 2},
            {"date": "2026-01-02", "open": 2, "high": 2, "low": 2, "close": 2},
        ]
    }


def test_formal_backtest_requires_explicit_cost_and_sizing_confirmation(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(store=store)

    blocked = app.call_tool(
        "run_backtest",
        {"strategy": _strategy(), "data": _data(), "run_mode": "formal"},
    )
    completed = app.call_tool(
        "run_backtest",
        {
            "strategy": _strategy(),
            "data": _data(),
            "run_mode": "formal",
            "confirm_cost_profile": True,
            "confirm_position_sizing": True,
        },
    )

    assert blocked["isError"] is True
    assert "确认" in blocked["structuredContent"]["error"]
    assert completed["isError"] is False
