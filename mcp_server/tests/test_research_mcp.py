from pathlib import Path

from mcp_server.server import McpApplication, tool_definitions
from mcp_server.services.research import InstrumentResearchService
from mcp_server.storage import SQLiteStore


class TinyProvider:
    provider_id = "fixture"
    skill_name = "a-stock-data"
    skill_version = "3.6.0"

    def collect(self, instrument, sections, as_of=None, refresh=False):
        bars = [
            {"date": "2026-08-10", "open": 1, "high": 2, "low": 1, "close": 1.5},
            {"date": "2026-08-11", "open": 1.5, "high": 2, "low": 1.4, "close": 1.8},
        ]
        return {
            "market": {
                "data": {"name": "测试ETF", "price": 1.8, "pe_ttm": 8, "pb": 1.1},
                "provenance": {"source_name": "fixture", "source_url": "test://quote"},
                "status": "ok",
            },
            "bars": {
                "data": bars,
                "provenance": {"source_name": "fixture", "source_url": "test://bars"},
                "status": "ok",
            },
        }


def test_mcp_catalog_exposes_instrument_research_tools():
    names = {tool["name"] for tool in tool_definitions()}
    assert {
        "research_instrument",
        "get_market_data",
        "get_fundamentals",
        "get_valuation",
        "score_instrument",
        "get_research_context",
        "save_research_analysis",
        "get_research_snapshot",
        "list_research_snapshots",
        "get_research_evidence",
    }.issubset(names)


def test_mcp_research_persists_snapshot_and_returns_artifacts(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(
        store=store,
        research_service=InstrumentResearchService(TinyProvider()),
        artifact_root=tmp_path / "artifacts",
    )

    result = app.call_tool("research_instrument", {"code": "512890"})

    assert result["isError"] is False
    content = result["structuredContent"]
    assert content["snapshot_id"] == 1
    assert content["snapshot"]["instrument"]["instrument_type"] == "ETF"
    assert Path(content["artifacts"]["report"]).exists()
    assert store.get_research_snapshot(1)["snapshot"]["instrument"]["code"] == "512890"


def test_mcp_research_analysis_requires_current_evidence_context(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(
        store=store,
        research_service=InstrumentResearchService(TinyProvider()),
        artifact_root=tmp_path / "artifacts",
    )
    created = app.call_tool("research_instrument", {"code": "512890"})["structuredContent"]
    context = app.call_tool(
        "get_research_context", {"snapshot_id": created["snapshot_id"]}
    )["structuredContent"]
    analysis = {
        "mode": "single",
        "summary": [
            {
                "text": "fixture",
                "evidence_refs": [context["evidence_ids"][0]],
            }
        ],
        "strengths": [],
        "risks": [],
        "data_limitations": [],
        "conditional_observations": [],
    }
    saved = app.call_tool(
        "save_research_analysis",
        {
            "snapshot_id": created["snapshot_id"],
            "context_hash": context["context_hash"],
            "analysis": analysis,
        },
    )

    assert saved["isError"] is False
    assert saved["structuredContent"]["analysis_status"] == "saved"
    assert store.get_research_snapshot(created["snapshot_id"])["snapshot"]["schema_version"] == 1


def test_research_can_explicitly_attach_watchlist_context_without_changing_facts(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    store.add_watchlist_item("512890", instrument_type="ETF", note="ETF context")
    app = McpApplication(
        store=store,
        research_service=InstrumentResearchService(TinyProvider()),
        artifact_root=tmp_path / "artifacts",
    )

    result = app.call_tool(
        "research_instrument", {"code": "512890", "include_watchlist": True}
    )

    assert result["isError"] is False
    snapshot = result["structuredContent"]["snapshot"]
    assert snapshot["watchlist_context"]["items"][0]["note"] == "ETF context"
    assert store.get_research_snapshot(result["structuredContent"]["snapshot_id"])["snapshot"]["technical"] == snapshot["technical"]
