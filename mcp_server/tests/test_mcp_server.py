from mcp_server.server import McpApplication, McpStdioServer, tool_definitions
from mcp_server.storage import SQLiteStore


def test_mcp_catalog_exposes_watchlist_and_notification_tools(tmp_path):
    names = {tool["name"] for tool in tool_definitions()}

    assert {
        "watchlist_add",
        "watchlist_remove",
        "watchlist_list",
        "preview_daily_watchlist_report",
        "configure_daily_report",
        "send_test_notification",
        "get_notification_status",
    }.issubset(names)


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


def test_mcp_stdio_initialize_and_tools_list_are_json_rpc_results(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    server = McpStdioServer(McpApplication(store=store))

    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert initialized["result"]["capabilities"]["tools"] == {}
    assert any(tool["name"] == "watchlist_add" for tool in listed["result"]["tools"])
