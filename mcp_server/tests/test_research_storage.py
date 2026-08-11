import json
from pathlib import Path

from mcp_server.services.research_artifacts import write_research_artifacts
from mcp_server.storage import SQLiteStore


def _snapshot():
    return {
        "schema_version": 1,
        "instrument": {
            "code": "512890",
            "market": "SH",
            "instrument_type": "ETF",
            "name": "测试ETF",
        },
        "provenance": {
            "provider_id": "fixture",
            "skill_name": "a-stock-data",
            "skill_version": "3.6.0",
        },
        "evidence": [
            {
                "evidence_id": "research:price",
                "section": "market",
                "field": "price",
                "value": 1.23,
                "status": "ok",
            }
        ],
        "scores": {"status": "watch", "coverage": 0.6},
        "warnings": [],
    }


def test_research_snapshot_and_ai_analysis_are_separate(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    snapshot = _snapshot()

    record = store.save_research_snapshot(snapshot)
    loaded = store.get_research_snapshot(record["id"])
    context = store.get_research_context(record["id"])

    assert loaded["snapshot"] == snapshot
    assert context["context_hash"]
    assert "research:price" in context["evidence_ids"]

    analysis = {
        "mode": "single",
        "summary": [
            {"text": "只基于已保存证据", "evidence_refs": ["research:price"]}
        ],
        "strengths": [],
        "risks": [],
        "data_limitations": [],
        "conditional_observations": [],
    }
    saved = store.save_research_analysis(
        record["id"], context["context_hash"], analysis
    )

    assert saved["version"] == 1
    assert store.get_research_snapshot(record["id"])["snapshot"] == snapshot
    assert store.get_latest_research_analysis(record["id"])["analysis"] == analysis


def test_research_context_rejects_stale_hash_and_unknown_evidence(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    record = store.save_research_snapshot(_snapshot())

    try:
        store.save_research_analysis(record["id"], "stale", {})
    except ValueError as exc:
        assert "context" in str(exc)
    else:
        raise AssertionError("stale context should be rejected")

    context = store.get_research_context(record["id"])
    try:
        store.save_research_analysis(
            record["id"],
            context["context_hash"],
            {"evidence_refs": ["research:unknown"]},
        )
    except ValueError as exc:
        assert "evidence" in str(exc)
    else:
        raise AssertionError("unknown evidence should be rejected")


def test_research_artifacts_are_isolated_and_include_fact_files(tmp_path):
    first = write_research_artifacts(tmp_path, _snapshot(), snapshot_id=1)
    second = write_research_artifacts(tmp_path, _snapshot(), snapshot_id=2)

    assert first["artifact_dir"] != second["artifact_dir"]
    for paths in (first, second):
        assert Path(paths["report"]).exists()
        assert Path(paths["snapshot"]).exists()
        assert Path(paths["evidence"]).exists()
        assert Path(paths["analysis"]).exists()
        assert json.loads(Path(paths["snapshot"]).read_text(encoding="utf-8"))["instrument"]["code"] == "512890"
