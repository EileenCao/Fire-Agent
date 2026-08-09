import json
from pathlib import Path

from mcp_server.cli import main
from mcp_server.server import McpApplication, tool_definitions
from mcp_server.storage import SQLiteStore


def _strategy():
    return {
        "strategy_id": "contract-test",
        "version": "1.0.0",
        "name": "contract test",
        "universe": ["512890"],
        "frequency": "1d",
        "entry": {"rules": [{"type": "state", "left": "close", "right": 1}]},
        "exit": {"rules": []},
        "position_sizing": {"type": "all_in"},
        "benchmark": None,
        "risk_free_rate_annual": 0.02,
        "data_policy": {"source_name": "fixture", "source_version": "a-stock-data:3.6.0"},
    }


def _data():
    return {
        "512890": [
            {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
            {"date": "2026-01-02", "open": 10, "high": 11, "low": 10, "close": 11},
            {"date": "2026-01-03", "open": 11, "high": 12, "low": 11, "close": 12},
        ]
    }


def test_run_backtest_schema_exposes_explicit_confirmations_and_analysis_tools():
    schemas = {item["name"]: item["inputSchema"] for item in tool_definitions()}

    assert "confirm_benchmark" in schemas["run_backtest"]["properties"]
    assert "confirm_risk_free_rate" in schemas["run_backtest"]["properties"]
    assert {
        "get_backtest_report_context",
        "save_backtest_analysis",
        "prepare_strategy_revision",
    }.issubset(schemas)


def test_mcp_run_returns_isolated_artifacts_and_persists_status(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(store=store, artifact_root=tmp_path / "artifacts")

    response = app.call_tool(
        "run_backtest",
        {
            "strategy": _strategy(),
            "data": _data(),
            "confirm_benchmark": True,
            "confirm_risk_free_rate": True,
        },
    )

    assert response["isError"] is False
    content = response["structuredContent"]
    artifact_dir = Path(content["artifacts"]["artifact_dir"])
    assert artifact_dir.parent == tmp_path / "artifacts" / "latest"
    assert (artifact_dir / "result.json").exists()
    assert (artifact_dir / "report.md").exists()
    assert content["analysis_status"] == "pending"
    record = store.get_backtest_result(content["run_id"])
    assert record["artifact_dir"] == str(artifact_dir)
    assert record["analysis_status"] == "pending"


def test_mcp_run_rejects_missing_assumption_confirmation(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(store=store, artifact_root=tmp_path / "artifacts")

    response = app.call_tool("run_backtest", {"strategy": _strategy(), "data": _data()})

    assert response["isError"] is True
    assert "benchmark" in response["structuredContent"]["error"]
    assert "无风险利率" in response["structuredContent"]["error"]


def test_storage_migrates_artifact_and_analysis_tables(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()

    with store._connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(backtest_runs)")}
        strategy_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(strategy_versions)")
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {"artifact_dir", "analysis_status"}.issubset(columns)
    assert {
        "parent_version",
        "source_run_id",
        "change_set_json",
        "approval_diff_hash",
    }.issubset(strategy_columns)
    assert "backtest_analyses" in tables


def test_mcp_analysis_context_and_writeback_do_not_change_result_facts(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    app = McpApplication(store=store, artifact_root=tmp_path / "artifacts")
    run = app.call_tool(
        "run_backtest",
        {
            "strategy": _strategy(),
            "data": _data(),
            "confirm_benchmark": True,
            "confirm_risk_free_rate": True,
        },
    )["structuredContent"]
    run_id = run["run_id"]
    before = json.dumps(store.get_backtest_result(run_id)["result"], sort_keys=True)
    context = app.call_tool("get_backtest_report_context", {"run_id": run_id})["structuredContent"]
    reference = context["context"]["evidence_ids"][0]
    item = {"text": "有证据的判断", "evidence_refs": [reference]}
    analysis = {
        "summary": [item],
        "strengths": [item],
        "risks": [item],
        "data_limitations": [item],
        "experiments": [item],
    }

    saved = app.call_tool(
        "save_backtest_analysis",
        {"run_id": run_id, "context_hash": context["context_hash"], "analysis": analysis},
    )

    assert saved["isError"] is False
    assert saved["structuredContent"]["analysis_status"] == "saved"
    after = json.dumps(store.get_backtest_result(run_id)["result"], sort_keys=True)
    assert after == before
    assert json.loads(Path(saved["structuredContent"]["artifacts"]["analysis"]).read_text(encoding="utf-8"))["status"] == "saved"
