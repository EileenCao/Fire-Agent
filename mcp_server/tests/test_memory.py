import json
from datetime import datetime, timedelta, timezone

import pytest

from mcp_server.server import McpApplication, tool_definitions
from mcp_server.domain.strategy import StrategySpec
from mcp_server.storage import SQLiteStore


def _candidate(
    content="我的最大组合回撤容忍度是15%",
    topic_key="risk.max_portfolio_drawdown",
    scope_type="global",
    scope_key=None,
    memory_type="risk_preference",
    structured_value=None,
):
    return {
        "memory_type": memory_type,
        "scope_type": scope_type,
        "scope_key": scope_key,
        "topic_key": topic_key,
        "content": content,
        "structured_value": structured_value
        or {"value": 0.15, "unit": "ratio"},
        "source": {
            "kind": "user_statement",
            "summary": "用户明确说明风险承受范围",
        },
    }


def _app(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    return McpApplication(store=store), store


def test_memory_tools_are_exposed():
    names = {tool["name"] for tool in tool_definitions()}

    assert {
        "prepare_memory",
        "save_memory",
        "list_memories",
        "search_memories",
        "get_memory_context",
        "archive_memory",
        "forget_memory",
        "export_memories",
        "preview_memory_import",
        "import_memories",
    }.issubset(names)


def test_prepare_memory_normalizes_instrument_scope_and_returns_hash(tmp_path):
    app, _ = _app(tmp_path)

    result = app.call_tool(
        "prepare_memory",
        {
            "candidate": _candidate(
                content="512890回撤到20%以内我会分批买入",
                topic_key="habit.buy_on_drawdown",
                scope_type="instrument",
                scope_key="SH512890",
                memory_type="behavioral_habit",
                structured_value={"threshold": 0.2, "unit": "ratio"},
            )
        },
    )

    assert result["isError"] is False
    candidate = result["structuredContent"]["candidate"]
    assert candidate["scope_key"] == "SH512890"
    assert result["structuredContent"]["approval_hash"]
    assert result["structuredContent"]["conflicts"] == []


def test_save_memory_requires_exact_user_confirmation(tmp_path):
    app, store = _app(tmp_path)
    prepared = app.call_tool(
        "prepare_memory", {"candidate": _candidate()}
    )["structuredContent"]

    rejected = app.call_tool(
        "save_memory",
        {
            "candidate": prepared["candidate"],
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": False,
        },
    )
    assert rejected["isError"] is True
    assert store.list_memories() == []

    saved = app.call_tool(
        "save_memory",
        {
            "candidate": prepared["candidate"],
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": True,
        },
    )
    assert saved["isError"] is False
    assert saved["structuredContent"]["memory"]["status"] == "active"
    assert len(store.list_memories()) == 1


def test_save_memory_rejects_tampered_candidate_hash(tmp_path):
    app, store = _app(tmp_path)
    prepared = app.call_tool(
        "prepare_memory", {"candidate": _candidate()}
    )["structuredContent"]
    tampered = dict(prepared["candidate"], content="被篡改的偏好")

    result = app.call_tool(
        "save_memory",
        {
            "candidate": tampered,
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": True,
        },
    )

    assert result["isError"] is True
    assert store.list_memories() == []


def test_search_memories_indexes_tags(tmp_path):
    app, store = _app(tmp_path)
    candidate = _candidate(
        content="回测报告指标偏好",
        topic_key="backtest.report_metrics",
        memory_type="process_preference",
    )
    candidate["tags"] = ["cashneutral", "TWR"]
    prepared = app.call_tool(
        "prepare_memory", {"candidate": candidate}
    )["structuredContent"]

    saved = app.call_tool(
        "save_memory",
        {
            "candidate": prepared["candidate"],
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": True,
        },
    )

    assert saved["isError"] is False
    assert [item["memory_id"] for item in store.search_memories("cashneutral")] == [
        saved["structuredContent"]["memory"]["memory_id"]
    ]


def test_confirmed_conflict_supersedes_previous_memory(tmp_path):
    app, store = _app(tmp_path)
    first = app.call_tool(
        "prepare_memory", {"candidate": _candidate()}
    )["structuredContent"]
    first_saved = app.call_tool(
        "save_memory",
        {
            "candidate": first["candidate"],
            "approval_hash": first["approval_hash"],
            "user_confirmed": True,
        },
    )["structuredContent"]["memory"]

    second = app.call_tool(
        "prepare_memory",
        {
            "candidate": _candidate(
                content="我的最大组合回撤容忍度改为10%",
                structured_value={"value": 0.10, "unit": "ratio"},
            )
        },
    )["structuredContent"]
    assert second["conflicts"][0]["memory_id"] == first_saved["memory_id"]

    saved = app.call_tool(
        "save_memory",
        {
            "candidate": second["candidate"],
            "approval_hash": second["approval_hash"],
            "supersedes_ids": [first_saved["memory_id"]],
            "user_confirmed": True,
        },
    )

    assert saved["isError"] is False
    all_records = store.list_memories(include_inactive=True)
    assert {record["status"] for record in all_records} == {
        "active",
        "superseded",
    }
    assert store.list_memories()[0]["structured_value"]["value"] == 0.10

    forgotten = app.call_tool(
        "forget_memory",
        {"memory_id": first_saved["memory_id"], "user_confirmed": True},
    )
    assert forgotten["isError"] is False
    assert store.list_memories()[0]["structured_value"]["value"] == 0.10


def test_confirmed_conflict_supersedes_after_store_reopen(tmp_path):
    app, store = _app(tmp_path)
    first = app.call_tool(
        "prepare_memory", {"candidate": _candidate()}
    )["structuredContent"]
    first_saved = app.call_tool(
        "save_memory",
        {
            "candidate": first["candidate"],
            "approval_hash": first["approval_hash"],
            "user_confirmed": True,
        },
    )["structuredContent"]["memory"]

    reopened_store = SQLiteStore(tmp_path / "research.sqlite3")
    reopened_store.initialize()
    reopened_app = McpApplication(store=reopened_store)
    second = reopened_app.call_tool(
        "prepare_memory",
        {
            "candidate": _candidate(
                content="我的最大组合回撤容忍度改为10%",
                structured_value={"value": 0.10, "unit": "ratio"},
            )
        },
    )["structuredContent"]

    saved = reopened_app.call_tool(
        "save_memory",
        {
            "candidate": second["candidate"],
            "approval_hash": second["approval_hash"],
            "supersedes_ids": [first_saved["memory_id"]],
            "user_confirmed": True,
        },
    )

    assert saved["isError"] is False
    assert reopened_store.list_memories()[0]["structured_value"]["value"] == 0.10


def test_memory_context_prefers_specific_scope_and_marks_review_due(tmp_path):
    app, store = _app(tmp_path)
    global_candidate = app.call_tool(
        "prepare_memory", {"candidate": _candidate()}
    )["structuredContent"]
    app.call_tool(
        "save_memory",
        {
            "candidate": global_candidate["candidate"],
            "approval_hash": global_candidate["approval_hash"],
            "user_confirmed": True,
        },
    )
    instrument_candidate = app.call_tool(
        "prepare_memory",
        {
            "candidate": _candidate(
                content="512890的风险预算是10%",
                scope_type="instrument",
                scope_key="512890",
                structured_value={"value": 0.10, "unit": "ratio"},
            )
        },
    )["structuredContent"]
    instrument = app.call_tool(
        "save_memory",
        {
            "candidate": instrument_candidate["candidate"],
            "approval_hash": instrument_candidate["approval_hash"],
            "user_confirmed": True,
        },
    )["structuredContent"]["memory"]
    due_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE memory_items SET review_due_at = ? WHERE id = ?",
            (due_at, instrument["memory_id"]),
        )

    result = app.call_tool(
        "get_memory_context",
        {"scope": {"strategy_id": "rsi", "instruments": ["SH512890"]}},
    )

    assert result["isError"] is False
    context = result["structuredContent"]
    assert context["memories"] == []
    assert context["review_due"][0]["memory_id"] == instrument["memory_id"]


def test_memory_search_archive_and_forget(tmp_path):
    app, store = _app(tmp_path)
    prepared = app.call_tool(
        "prepare_memory",
        {
            "candidate": _candidate(
                content="我不追涨，宁可错过也不追高",
                topic_key="principle.no_chasing",
                memory_type="trading_principle",
                structured_value=None,
            )
        },
    )["structuredContent"]
    saved = app.call_tool(
        "save_memory",
        {
            "candidate": prepared["candidate"],
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": True,
        },
    )["structuredContent"]["memory"]

    found = app.call_tool("search_memories", {"query": "追涨"})
    assert found["structuredContent"]["memories"][0]["memory_id"] == saved["memory_id"]

    archived = app.call_tool(
        "archive_memory",
        {"memory_id": saved["memory_id"], "user_confirmed": True},
    )
    assert archived["isError"] is False
    assert store.list_memories() == []
    assert len(store.list_memories(include_inactive=True)) == 1

    forgotten = app.call_tool(
        "forget_memory",
        {"memory_id": saved["memory_id"], "user_confirmed": True},
    )
    assert forgotten["isError"] is False
    assert store.list_memories(include_inactive=True) == []


def test_forget_memory_reports_analysis_references(tmp_path):
    app, store = _app(tmp_path)
    spec = StrategySpec(
        strategy_id="memory-reference",
        version="v1",
        name="memory reference",
        universe=["512890"],
        frequency="1d",
        entry={},
        exit={},
        position_sizing={"type": "all_in"},
    )
    run = store.save_backtest_run(spec, {"scenarios": {}})
    candidate = _candidate()
    candidate["source"]["run_ids"] = [run["id"]]
    prepared = app.call_tool("prepare_memory", {"candidate": candidate})["structuredContent"]
    saved = app.call_tool(
        "save_memory",
        {
            "candidate": prepared["candidate"],
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": True,
        },
    )["structuredContent"]["memory"]
    store.save_backtest_analysis(
        run["id"], "context-hash", {"memory_refs": [saved["memory_ref"]]}
    )

    forgotten = app.call_tool(
        "forget_memory",
        {"memory_id": saved["memory_id"], "user_confirmed": True},
    )

    assert forgotten["isError"] is False
    payload = forgotten["structuredContent"]
    assert payload["historical_references"] == [run["id"]]
    assert payload["analysis_references"] == [{"run_id": run["id"], "version": 1}]


def test_memory_export_import_round_trip_requires_preview_hash(tmp_path):
    app, store = _app(tmp_path)
    prepared = app.call_tool(
        "prepare_memory", {"candidate": _candidate()}
    )["structuredContent"]
    app.call_tool(
        "save_memory",
        {
            "candidate": prepared["candidate"],
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": True,
        },
    )
    export_path = tmp_path / "exports" / "memories.json"
    exported = app.call_tool(
        "export_memories", {"output_path": str(export_path)}
    )
    assert exported["isError"] is False
    assert json.loads(export_path.read_text(encoding="utf-8"))["schema_version"] == 1

    other_app, other_store = _app(tmp_path / "other")
    preview = other_app.call_tool(
        "preview_memory_import", {"input_path": str(export_path)}
    )
    assert preview["isError"] is False
    import_hash = preview["structuredContent"]["import_hash"]

    rejected = other_app.call_tool(
        "import_memories",
        {"input_path": str(export_path), "import_hash": import_hash},
    )
    assert rejected["isError"] is True
    assert other_store.list_memories() == []

    imported = other_app.call_tool(
        "import_memories",
        {
            "input_path": str(export_path),
            "import_hash": import_hash,
            "user_confirmed": True,
        },
    )
    assert imported["isError"] is False
    assert len(other_store.list_memories()) == 1


def test_memory_import_preview_detects_active_topic_conflict(tmp_path):
    source_app, _ = _app(tmp_path / "source")
    prepared = source_app.call_tool(
        "prepare_memory", {"candidate": _candidate()}
    )["structuredContent"]
    source_app.call_tool(
        "save_memory",
        {
            "candidate": prepared["candidate"],
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": True,
        },
    )
    export_path = tmp_path / "memories.json"
    source_app.call_tool("export_memories", {"output_path": str(export_path)})

    other_app, other_store = _app(tmp_path / "other-conflict")
    conflicting = other_app.call_tool(
        "prepare_memory",
        {
            "candidate": _candidate(
                content="新的回撤偏好",
                structured_value={"value": 0.08, "unit": "ratio"},
            )
        },
    )["structuredContent"]
    assert conflicting["candidate"]["content"] == "新的回撤偏好"
    other_app.call_tool(
        "save_memory",
        {
            "candidate": conflicting["candidate"],
            "approval_hash": conflicting["approval_hash"],
            "user_confirmed": True,
        },
    )

    preview = other_app.call_tool(
        "preview_memory_import", {"input_path": str(export_path)}
    )
    assert preview["isError"] is False
    assert len(preview["structuredContent"]["conflicts"]) == 1

    rejected = other_app.call_tool(
        "import_memories",
        {
            "input_path": str(export_path),
            "import_hash": preview["structuredContent"]["import_hash"],
            "user_confirmed": True,
        },
    )
    assert rejected["isError"] is True
    assert len(other_store.list_memories()) == 1


def test_backtest_report_context_includes_relevant_memory_context(tmp_path):
    app, _ = _app(tmp_path)
    prepared = app.call_tool(
        "prepare_memory", {"candidate": _candidate()}
    )["structuredContent"]
    app.call_tool(
        "save_memory",
        {
            "candidate": prepared["candidate"],
            "approval_hash": prepared["approval_hash"],
            "user_confirmed": True,
        },
    )
    run = app.call_tool(
        "run_backtest",
        {
            "strategy": {
                "strategy_id": "memory-context",
                "version": "1.0.0",
                "name": "memory context",
                "universe": ["512890"],
                "frequency": "1d",
                "entry": {"rules": []},
                "exit": {"rules": []},
                "position_sizing": {"type": "all_in"},
                "benchmark": None,
                "risk_free_rate_annual": 0.02,
            },
            "data": {
                "512890": [
                    {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
                    {"date": "2026-01-02", "open": 10, "high": 11, "low": 10, "close": 11},
                ]
            },
            "confirm_benchmark": True,
            "confirm_risk_free_rate": True,
        },
    )["structuredContent"]

    context = app.call_tool(
        "get_backtest_report_context", {"run_id": run["run_id"]}
    )

    assert context["isError"] is False
    memories = context["structuredContent"]["context"]["memory_context"]["memories"]
    assert memories[0]["topic_key"] == "risk.max_portfolio_drawdown"
    assert context["structuredContent"]["context"]["memory_refs"]


def test_backtest_analysis_rejects_unknown_memory_reference(tmp_path):
    app, _ = _app(tmp_path)
    run = app.call_tool(
        "run_backtest",
        {
            "strategy": {
                "strategy_id": "memory-analysis",
                "version": "1.0.0",
                "name": "memory analysis",
                "universe": ["512890"],
                "frequency": "1d",
                "entry": {"rules": []},
                "exit": {"rules": []},
                "position_sizing": {"type": "all_in"},
                "benchmark": None,
                "risk_free_rate_annual": 0.02,
            },
            "data": {
                "512890": [
                    {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
                    {"date": "2026-01-02", "open": 10, "high": 11, "low": 10, "close": 11},
                ]
            },
            "confirm_benchmark": True,
            "confirm_risk_free_rate": True,
        },
    )["structuredContent"]
    context = app.call_tool(
        "get_backtest_report_context", {"run_id": run["run_id"]}
    )["structuredContent"]
    evidence_ref = context["context"]["evidence_ids"][0]
    item = {
        "text": "依据记忆给出风险提示",
        "evidence_refs": [evidence_ref],
        "memory_refs": ["memory:999:v1"],
    }
    analysis = {
        "summary": [item],
        "strengths": [item],
        "risks": [item],
        "data_limitations": [item],
        "experiments": [item],
    }

    saved = app.call_tool(
        "save_backtest_analysis",
        {
            "run_id": run["run_id"],
            "context_hash": context["context_hash"],
            "analysis": analysis,
        },
    )

    assert saved["isError"] is True
    assert "memory" in saved["structuredContent"]["error"]
