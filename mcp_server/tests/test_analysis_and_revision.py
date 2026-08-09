import copy
import hashlib
import json
from pathlib import Path

import pytest

from mcp_server.domain.strategy import StrategySpec
from mcp_server.server import McpApplication
from mcp_server.services.analysis import build_report_context, save_analysis_and_render
from mcp_server.services.strategy_revision import prepare_strategy_revision
from mcp_server.storage import SQLiteStore


def _strategy(version="1.0.0"):
    return StrategySpec.from_dict(
        {
            "strategy_id": "analysis-test",
            "version": version,
            "name": "analysis test",
            "universe": ["512890"],
            "frequency": "1d",
            "entry": {"rules": []},
            "exit": {"rules": []},
            "position_sizing": {"type": "all_in"},
            "benchmark": None,
            "risk_free_rate_annual": 0.02,
            "data_policy": {"source_version": "a-stock-data:3.6.0"},
        }
    )


def _result():
    return {
        "strategy_id": "analysis-test",
        "strategy_version": "1.0.0",
        "run_mode": "latest",
        "provenance": {"source_version": "a-stock-data:3.6.0"},
        "scenarios": {
            "default": {
                "equity_curve": {"2026-01-01": 1000, "2026-01-02": 1010},
                "trades": [],
                "metrics": {"final_equity": 1010, "max_drawdown": 0},
                "warnings": [],
            }
        },
        "warnings": [],
        "validation": {},
    }


def _analysis(ref):
    item = {"text": "策略短样本内保持稳定", "evidence_refs": [ref]}
    return {
        "summary": [item],
        "strengths": [item],
        "risks": [item],
        "data_limitations": [item],
        "experiments": [
            {
                "title": "延长测试区间",
                "text": "检查稳定性",
                "evidence_refs": [ref],
            }
        ],
    }


def test_report_context_is_bounded_and_has_stable_hash():
    record = {"id": 7, "strategy_id": "analysis-test", "strategy_version": "1.0.0", "result": _result()}

    first = build_report_context(record, [])
    second = build_report_context(record, [])

    assert first["context_hash"] == second["context_hash"]
    assert first["context"]["evidence_ids"]
    assert len(json.dumps(first["context"], ensure_ascii=False)) < 64000


def test_analysis_rejects_unknown_evidence_and_stale_context(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    run = store.save_backtest_run(_strategy(), _result())
    context = build_report_context(store.get_backtest_result(run["id"]), [])

    with pytest.raises(ValueError, match="证据"):
        save_analysis_and_render(
            store,
            run["id"],
            context["context_hash"],
            _analysis("evidence:unknown"),
        )
    with pytest.raises(ValueError, match="上下文"):
        save_analysis_and_render(
            store,
            run["id"],
            "stale-hash",
            _analysis(context["context"]["evidence_ids"][0]),
        )


def test_revision_diff_requires_explicit_approval_and_matches_values():
    base = _strategy().to_dict()
    proposed = copy.deepcopy(base)
    proposed["position_sizing"] = {"type": "cash_pct", "fraction": 0.5}

    diff = prepare_strategy_revision(base, proposed, source_run_id=7)

    assert {item["path"] for item in diff["changes"]} == {
        "$.position_sizing.type",
        "$.position_sizing.fraction",
    }
    assert diff["diff_hash"]
    assert "benchmark" in diff["unchanged_assumptions"]


def test_mcp_does_not_save_revision_without_final_user_approval(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    base = _strategy()
    store.save_strategy_version(base, status="approved")
    app = McpApplication(store=store)
    proposed = dict(base.to_dict(), version="2.0.0", position_sizing={"type": "cash_pct", "fraction": 0.5})
    diff = prepare_strategy_revision(base.to_dict(), proposed)

    blocked = app.call_tool(
        "save_strategy_version",
        {
            "strategy": proposed,
            "status": "approved",
            "parent_version": "1.0.0",
            "change_set": diff["changes"],
            "approved_diff_hash": diff["diff_hash"],
        },
    )
    assert blocked["isError"] is True
    assert "最终批准" in blocked["structuredContent"]["error"]

    approved = app.call_tool(
        "save_strategy_version",
        {
            "strategy": proposed,
            "status": "approved",
            "parent_version": "1.0.0",
            "change_set": diff["changes"],
            "approved_diff_hash": diff["diff_hash"],
            "user_confirmed": True,
        },
    )
    assert approved["isError"] is False
    assert store.get_strategy_version("analysis-test", "2.0.0")["parent_version"] == "1.0.0"
