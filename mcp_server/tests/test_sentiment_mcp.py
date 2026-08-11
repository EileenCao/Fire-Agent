import json
from pathlib import Path

from mcp_server.server import McpApplication, tool_definitions
from mcp_server.storage import SQLiteStore


def _app(tmp_path, provider=None):
    store = SQLiteStore(tmp_path / "sentiment.sqlite3")
    store.initialize()
    return McpApplication(
        store=store,
        artifact_root=tmp_path / "artifacts",
        sentiment_provider=provider,
    )


def _document():
    return {
        "platform": "xueqiu",
        "source_id": "author-1",
        "author_id": "author-1",
        "author_name": "测试作者",
        "document_type": "blogger",
        "canonical_url": "https://example.test/post/1",
        "published_at": "2026-08-10T10:00:00+08:00",
        "collected_at": "2026-08-10T10:05:00+08:00",
        "content": "512890 未来走势偏强",
        "summary": "512890 未来走势偏强",
        "targets": ["512890"],
    }


def test_mcp_sentiment_manual_flow_persists_snapshot_and_evidence(tmp_path):
    app = _app(tmp_path)
    names = {tool["name"] for tool in tool_definitions()}
    assert {
        "sentiment_source_upsert",
        "collect_sentiment_documents",
        "ingest_sentiment_document",
        "get_sentiment_extraction_context",
        "save_sentiment_extraction",
        "build_sentiment_snapshot",
        "get_sentiment_snapshot",
        "list_sentiment_snapshots",
        "get_sentiment_evidence",
        "evaluate_sentiment_authors",
        "prepare_strategy_candidate_from_opinion",
    }.issubset(names)

    ingested = app.call_tool("ingest_sentiment_document", {"document": _document()})
    assert ingested["isError"] is False
    document_id = ingested["structuredContent"]["document"]["document_id"]
    context = app.call_tool(
        "get_sentiment_extraction_context", {"document_ids": [document_id]}
    )["structuredContent"]
    saved = app.call_tool(
        "save_sentiment_extraction",
        {
            "document_id": document_id,
            "context_hash": context["context_hash"],
            "extraction_model": "agent-fixture",
            "prompt_version": "sentiment-extract-v1",
            "extraction": {
                "claims": [
                    {
                        "direction": 1,
                        "confidence": 0.8,
                        "relevance": 1,
                        "time_horizon": 5,
                        "targets": ["512890"],
                        "strategy_statement": {"entry": "突破"},
                    }
                ]
            },
        },
    )
    assert saved["isError"] is False
    snapshot = app.call_tool(
        "build_sentiment_snapshot",
        {
            "document_ids": [document_id],
            "scope_type": "instrument",
            "scope_key": "512890",
            "snapshot_date": "2026-08-11",
            "cutoff": "15:00",
            "trading_dates": ["2026-08-10", "2026-08-11"],
        },
    )
    assert snapshot["isError"] is False
    content = snapshot["structuredContent"]
    assert content["snapshot"]["factors"]["5d"]["blogger_consensus_equal"]["value"] > 0
    assert Path(content["artifacts"]["report"]).exists()
    assert content["snapshot_id"] == 1

    evidence = app.call_tool(
        "get_sentiment_evidence", {"snapshot_id": content["snapshot_id"]}
    )
    assert evidence["isError"] is False
    assert evidence["structuredContent"]["evidence"]


def test_mcp_sentiment_collection_without_provider_is_explicit(tmp_path):
    app = _app(tmp_path)
    app.call_tool(
        "sentiment_source_upsert",
        {"source_id": "news", "platform": "eastmoney", "source_type": "news"},
    )
    result = app.call_tool(
        "collect_sentiment_documents",
        {"source_id": "news", "code": "512890", "market": "SH"},
    )
    assert result["isError"] is True
    assert "provider" in result["structuredContent"]["error"]


def test_sentiment_snapshot_can_feed_backtest_and_formal_gate(tmp_path):
    app = _app(tmp_path)
    snapshot = {
        "profile": "sentiment-baseline-v1",
        "snapshot_date": "2026-08-10",
        "cutoff": "15:00",
        "scope": {"type": "instrument", "key": "512890"},
        "factors": {
            "5d": {"news_event_sentiment": {"value": 80, "percentile": 0.9}}
        },
        "backtest_eligibility": {
            "status": "exploratory_only",
            "eligible": False,
            "coverage": 0.1,
            "valid_snapshot_count": 1,
        },
        "evidence": [],
    }
    snapshot_id = app.store.save_sentiment_snapshot(snapshot)["id"]
    strategy = {
        "strategy_id": "sentiment-fixture",
        "version": "1.0.0",
        "name": "sentiment fixture",
        "universe": ["512890"],
        "frequency": "1d",
        "indicators": [
            {
                "id": "news_5d",
                "type": "sentiment",
                "factor": "news_event_sentiment",
                "scope": "instrument",
                "horizon": 5,
                "representation": "raw",
                "cutoff": "15:00",
                "profile": "sentiment-baseline-v1",
            }
        ],
        "entry": {"rules": []},
        "exit": {"rules": []},
        "position_sizing": {"type": "all_in"},
        "benchmark": None,
        "risk_free_rate_annual": 0.02,
        "cost_profile": {"template": "theoretical", "version": "1.0.0"},
    }
    response = app.call_tool(
        "run_backtest",
        {
            "strategy": strategy,
            "data": {
                "512890": [
                    {"date": "2026-08-10", "open": 1, "high": 1, "low": 1, "close": 1},
                    {"date": "2026-08-11", "open": 1, "high": 1, "low": 1, "close": 1},
                ]
            },
            "sentiment_snapshot_ids": [snapshot_id],
            "confirm_benchmark": True,
            "confirm_risk_free_rate": True,
        },
    )

    assert response["isError"] is False
    assert response["structuredContent"]["result"]["sentiment_snapshot_ids"] == [snapshot_id]

    formal = app.call_tool(
        "run_backtest",
        {
            "strategy": strategy,
            "data": {"512890": [{"date": "2026-08-10", "open": 1, "high": 1, "low": 1, "close": 1}]},
            "sentiment_snapshot_ids": [snapshot_id],
            "run_mode": "formal",
            "confirm_cost_profile": True,
            "confirm_position_sizing": True,
            "confirm_benchmark": True,
            "confirm_risk_free_rate": True,
        },
    )
    assert formal["isError"] is True
    assert "门槛" in formal["structuredContent"]["error"]


def test_sentiment_snapshot_uses_previous_252_snapshots_for_percentile(tmp_path):
    app = _app(tmp_path)
    for _ in range(20):
        app.store.save_sentiment_snapshot(
            {
                "profile": "sentiment-baseline-v1",
                "scope": {"type": "instrument", "key": "512890"},
                "snapshot_date": "2026-08-10",
                "cutoff": "15:00",
                "factors": {
                    "5d": {
                        "blogger_consensus_equal": {
                            "status": "ok",
                            "value": -100,
                        }
                    }
                },
            }
        )
    ingested = app.call_tool("ingest_sentiment_document", {"document": _document()})
    document_id = ingested["structuredContent"]["document"]["document_id"]
    context = app.call_tool(
        "get_sentiment_extraction_context", {"document_ids": [document_id]}
    )["structuredContent"]
    app.call_tool(
        "save_sentiment_extraction",
        {
            "document_id": document_id,
            "context_hash": context["context_hash"],
            "extraction_model": "fixture",
            "prompt_version": "v1",
            "extraction": {
                "claims": [
                    {
                        "direction": 1,
                        "time_horizon": 5,
                        "targets": ["512890"],
                    }
                ]
            },
        },
    )
    result = app.call_tool(
        "build_sentiment_snapshot",
        {
            "document_ids": [document_id],
            "scope_type": "instrument",
            "scope_key": "512890",
            "snapshot_date": "2026-08-11",
            "trading_dates": ["2026-08-10", "2026-08-11"],
        },
    )

    assert result["isError"] is False
    assert (
        result["structuredContent"]["snapshot"]["factors"]["5d"]
        ["blogger_consensus_equal"]["percentile"]
        == 1.0
    )
