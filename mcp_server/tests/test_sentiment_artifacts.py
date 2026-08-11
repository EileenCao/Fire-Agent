import json
from pathlib import Path

from mcp_server.services.sentiment_artifacts import write_sentiment_artifacts


def test_sentiment_artifacts_are_isolated_and_keep_fact_files(tmp_path):
    snapshot = {
        "profile": "sentiment-baseline-v1",
        "snapshot_date": "2026-08-10",
        "cutoff": "15:00",
        "scope": {"type": "instrument", "key": "512890"},
        "factors": {"5d": {"news_event_sentiment": {"value": 20}}},
        "evidence": [],
        "warnings": [],
    }
    first = write_sentiment_artifacts(tmp_path, snapshot, run_id=1)
    second = write_sentiment_artifacts(tmp_path, snapshot, run_id=2)

    assert first["artifact_dir"] != second["artifact_dir"]
    assert Path(first["report"]).exists()
    assert Path(first["snapshot"]).exists()
    assert Path(first["evidence"]).exists()
    assert Path(first["author_performance"]).exists()
    assert json.loads(Path(first["snapshot"]).read_text(encoding="utf-8"))["scope"]["key"] == "512890"
