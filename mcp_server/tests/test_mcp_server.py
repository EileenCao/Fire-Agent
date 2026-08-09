from mcp_server.server import McpApplication, McpStdioServer, tool_definitions
from mcp_server.services.historical_data import HistoricalDataResult
from mcp_server.storage import SQLiteStore


def _strategy_payload(version="1.0.0"):
    return {
        "strategy_id": "ma-trend",
        "version": version,
        "name": "均线趋势",
        "universe": ["512890"],
        "frequency": "1d",
        "entry": {"rules": [{"type": "state", "left": "close", "right": 1}]},
        "exit": {"rules": [{"type": "state", "left": "close", "right": 1}]},
        "position_sizing": {"type": "all_in"},
    }


def test_mcp_catalog_exposes_watchlist_and_notification_tools(tmp_path):
    names = {tool["name"] for tool in tool_definitions()}

    assert {
        "validate_strategy",
        "save_strategy_version",
        "activate_strategy",
        "prepare_backtest_data",
        "run_backtest",
        "get_backtest_result",
        "compare_backtests",
        "observe_active_strategy",
        "get_signal_evidence",
        "watchlist_add",
        "watchlist_remove",
        "watchlist_list",
        "preview_daily_watchlist_report",
        "configure_daily_report",
        "send_test_notification",
        "get_notification_status",
    }.issubset(names)
    schemas = {tool["name"]: tool["inputSchema"] for tool in tool_definitions()}
    assert "data" not in schemas["run_backtest"].get("required", [])
    assert "data" not in schemas["prepare_backtest_data"].get("required", [])


def test_mcp_watchlist_call_returns_structured_result(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(store=store, market_provider=None, notifier=None)

    result = app.call_tool(
        "watchlist_add",
        {"code": "512890", "instrument_type": "ETF", "note": "红利"},
    )

    assert result["isError"] is False
    assert result["structuredContent"]["item"]["code"] == "512890"
    assert result["structuredContent"]["item"]["instrument_type"] == "ETF"


def test_mcp_can_validate_save_and_activate_strategy(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(store=store)

    validated = app.call_tool("validate_strategy", {"strategy": _strategy_payload()})
    saved = app.call_tool(
        "save_strategy_version",
        {"strategy": _strategy_payload(), "status": "approved"},
    )
    activated = app.call_tool(
        "activate_strategy", {"strategy_id": "ma-trend", "version": "1.0.0"}
    )

    assert validated["structuredContent"]["valid"] is True
    assert saved["structuredContent"]["strategy"]["version"] == "1.0.0"
    assert activated["structuredContent"]["active"] is True
    assert store.get_active_strategy().strategy_id == "ma-trend"


def test_mcp_persists_backtest_result_and_exposes_evidence(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(store=store)
    strategy = dict(_strategy_payload(), entry={"rules": [{"type": "cross_above", "left": "sma_2", "right": "sma_3"}]}, exit={"rules": [{"type": "cross_below", "left": "sma_2", "right": "sma_3"}]}, data_policy={"source_version": "a-stock-data:3.6.0"})
    data = {"512890": [
        {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-04", "open": 10, "high": 12, "low": 10, "close": 12},
        {"date": "2026-01-05", "open": 20, "high": 20, "low": 18, "close": 18},
    ]}

    run = app.call_tool("run_backtest", {"strategy": strategy, "data": data})
    run_id = run["structuredContent"]["run_id"]
    loaded = app.call_tool("get_backtest_result", {"run_id": run_id})
    compared = app.call_tool("compare_backtests", {"run_ids": [run_id]})
    evidence = app.call_tool("get_signal_evidence", {"run_id": run_id})

    assert loaded["structuredContent"]["result"]["provenance"]["source_version"] == "a-stock-data:3.6.0"
    assert compared["structuredContent"][0]["run_id"] == run_id
    assert evidence["structuredContent"]["run_id"] == run_id


def test_mcp_fetches_data_when_run_backtest_data_is_omitted(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    data = {
        "512890": [
            {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
            {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
            {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10},
            {"date": "2026-01-04", "open": 10, "high": 12, "low": 10, "close": 12},
            {"date": "2026-01-05", "open": 20, "high": 20, "low": 18, "close": 18},
        ]
    }

    class FakeProvider:
        def fetch(self, codes, start_date, end_date):
            return HistoricalDataResult(
                data=data,
                provenance={
                    "source_name": "fake-a-stock-data",
                    "source_version": "a-stock-data:3.6.0",
                    "skill_name": "a-stock-data",
                    "skill_version": "3.6.0",
                    "price_basis": "adjusted",
                },
            )

    app = McpApplication(store=store, historical_data_provider=FakeProvider())
    result = app.call_tool("run_backtest", {"strategy": _strategy_payload()})

    assert result["isError"] is False
    assert result["structuredContent"]["result"]["provenance"]["source_name"] == "fake-a-stock-data"


def test_mcp_stdio_initialize_and_tools_list_are_json_rpc_results(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    server = McpStdioServer(McpApplication(store=store))

    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert initialized["result"]["capabilities"]["tools"] == {}
    assert any(tool["name"] == "watchlist_add" for tool in listed["result"]["tools"])
