"""Bounded backtest context and evidence-checked AI analysis persistence."""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from mcp_server.services.artifacts import write_backtest_artifacts


CONTEXT_MAX_BYTES = 64 * 1024
ANALYSIS_KEYS = (
    "summary",
    "strengths",
    "risks",
    "data_limitations",
    "experiments",
)


def build_report_context(
    record: Dict[str, Any],
    evidence: Iterable[Dict[str, Any]],
    max_bytes: int = CONTEXT_MAX_BYTES,
    memory_context: Dict[str, Any] = None,
) -> Dict[str, Any]:
    result = record.get("result") or {}
    run_id = int(record.get("id") or record.get("run_id"))
    evidence_rows = list(evidence or [])
    evidence_ids = {str(row.get("signal_id")) for row in evidence_rows if row.get("signal_id")}
    scenarios = {}
    for scenario_name, scenario in result.get("scenarios", {}).items():
        metrics = dict(scenario.get("metrics") or {})
        evidence_ids.update(
            "metric:{}:{}".format(scenario_name, key) for key in metrics
        )
        trades = list(scenario.get("trades") or [])
        representative = _representative_trades(trades, run_id, scenario_name)
        evidence_ids.update(
            item["evidence_id"] for item in representative if item.get("evidence_id")
        )
        scenarios[scenario_name] = {
            "metrics": metrics,
            "representative_trades": representative,
            "trade_count": len(trades),
        }
    warning_categories = _warning_categories(result)
    evidence_ids.update("warning:{}".format(key) for key in warning_categories)
    evidence_ids.add("validation:sample_split")
    memory_context = _normalize_memory_context(memory_context)
    memory_refs = sorted(
        item["memory_ref"]
        for key in ("memories", "review_due", "shadowed")
        for item in memory_context.get(key, [])
        if item.get("memory_ref")
    )
    context = {
        "run_id": run_id,
        "strategy_id": record.get("strategy_id") or result.get("strategy_id"),
        "strategy_version": record.get("strategy_version") or result.get("strategy_version"),
        "run_mode": result.get("run_mode"),
        "assumptions": result.get("assumptions", {}),
        "provenance": result.get("provenance", {}),
        "benchmark": result.get("benchmark_comparison") or result.get("benchmark"),
        "scenarios": scenarios,
        "validation": result.get("validation", {}),
        "warning_categories": warning_categories,
        "evidence_ids": sorted(evidence_ids),
        "memory_context": memory_context,
        "memory_refs": memory_refs,
    }
    serialized = _canonical(context)
    if len(serialized.encode("utf-8")) > max_bytes:
        context["provenance"] = _compact_dict(context.get("provenance", {}))
        context["validation"] = _compact_validation(context.get("validation", {}))
        for scenario in context["scenarios"].values():
            scenario["metrics"] = _compact_metrics(scenario.get("metrics", {}))
        serialized = _canonical(context)
    if len(serialized.encode("utf-8")) > max_bytes:
        raise ValueError("回测报告上下文超过大小限制")
    return {
        "context_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "context": context,
        "byte_size": len(serialized.encode("utf-8")),
        "evidence_count": len(context["evidence_ids"]),
    }


def _normalize_memory_context(memory_context: Dict[str, Any] = None) -> Dict[str, Any]:
    value = dict(memory_context or {})
    for key in ("memories", "review_due", "shadowed"):
        value[key] = list(value.get(key) or [])
    if not any(value[key] for key in ("memories", "review_due", "shadowed")):
        return {
            "memories": [],
            "review_due": [],
            "shadowed": [],
            "scope": {},
        }
    value.pop("context_hash", None)
    value.setdefault("scope", {})
    return value


def save_analysis_and_render(
    store,
    run_id: int,
    context_hash: str,
    analysis: Dict[str, Any],
) -> Dict[str, Any]:
    record = store.get_backtest_result(int(run_id))
    if record is None:
        raise ValueError("找不到回测运行记录：{}".format(run_id))
    evidence = store.list_signal_evidence(int(run_id))
    context = build_report_context(
        record,
        evidence,
        memory_context=store.get_memory_context(_memory_scope(record, evidence)),
    )
    if context["context_hash"] != context_hash:
        raise ValueError("回测报告上下文已过期，请重新读取上下文")
    normalized = _validate_analysis(
        analysis,
        set(context["context"]["evidence_ids"]),
        set(context["context"].get("memory_refs", [])),
    )
    saved = store.save_backtest_analysis(int(run_id), context_hash, normalized)
    normalized["version"] = saved["version"]
    normalized["status"] = "saved"
    artifact_dir = record.get("artifact_dir")
    if artifact_dir:
        base_dir = Path(artifact_dir).parent
    else:
        mode = (record.get("result") or {}).get("run_mode", "latest")
        base_dir = Path(store.path).parent / "artifacts" / ("formal" if mode == "formal" else "latest")
    artifacts = write_backtest_artifacts(
        base_dir,
        record["result"],
        int(run_id),
        created_at=record.get("created_at"),
        analysis=normalized,
    )
    store.update_backtest_artifacts(int(run_id), artifacts["artifact_dir"], "saved")
    return {
        "run_id": int(run_id),
        "analysis_version": saved["version"],
        "context_hash": context_hash,
        "analysis_status": "saved",
        "artifacts": artifacts,
    }


def _validate_analysis(
    analysis: Dict[str, Any], allowed: set, allowed_memory_refs: set = None
) -> Dict[str, Any]:
    if not isinstance(analysis, dict):
        raise ValueError("AI 分析必须是对象")
    normalized = {}
    for key in ANALYSIS_KEYS:
        values = analysis.get(key)
        if values is None:
            raise ValueError("AI 分析缺少字段：{}".format(key))
        if isinstance(values, str) or not isinstance(values, list):
            raise ValueError("AI 分析字段 {} 必须是数组".format(key))
        if key == "experiments" and len(values) > 3:
            raise ValueError("候选实验最多三个")
        normalized[key] = []
        for item in values:
            if not isinstance(item, dict):
                raise ValueError("AI 分析每条判断必须是对象")
            refs = item.get("evidence_refs")
            if not isinstance(refs, list) or not refs:
                raise ValueError("AI 分析每条判断必须提供证据引用")
            unknown = [str(ref) for ref in refs if str(ref) not in allowed]
            if unknown:
                raise ValueError("AI 分析引用未知证据：{}".format(", ".join(unknown)))
            memory_refs = item.get("memory_refs", [])
            if not isinstance(memory_refs, list):
                raise ValueError("AI 分析 memory_refs 必须是数组")
            unknown_memory = [
                str(ref)
                for ref in memory_refs
                if str(ref) not in (allowed_memory_refs or set())
            ]
            if unknown_memory:
                raise ValueError(
                    "AI 分析引用未知 memory 快照：{}".format(
                        ", ".join(unknown_memory)
                    )
                )
            normalized[key].append(dict(item))
    return normalized


def _representative_trades(trades: List[Dict[str, Any]], run_id: int, scenario: str):
    if not trades:
        return []
    indexes = list(range(min(3, len(trades))))
    indexes.extend(range(max(0, len(trades) - 3), len(trades)))
    pnl_indexes = sorted(
        range(len(trades)),
        key=lambda index: float(trades[index].get("pnl", 0.0) or 0.0),
    )
    indexes.extend(pnl_indexes[:1] + pnl_indexes[-1:])
    result = []
    seen = set()
    for index in indexes:
        if index in seen:
            continue
        seen.add(index)
        trade = dict(trades[index])
        trade["evidence_id"] = "{}:{}:{}".format(run_id, scenario, index)
        result.append(trade)
    return result[:8]


def _memory_scope(record: Dict[str, Any], evidence: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    instruments = sorted(
        {
            "{}{}".format(row.get("market", ""), row.get("code", ""))
            if row.get("market")
            else str(row.get("code", ""))
            for row in evidence
            if row.get("code")
        }
    )
    return {
        "strategy_id": record.get("strategy_id"),
        "instruments": instruments,
    }


def _warning_categories(result):
    values = list(result.get("warnings", []))
    for scenario in result.get("scenarios", {}).values():
        values.extend(scenario.get("warnings", []))
    categories = {}
    for value in sorted(set(str(item) for item in values if item)):
        category = "execution"
        if "停牌" in value:
            category = "suspension"
        elif "缺失" in value or "数据" in value:
            category = "data_quality"
        elif "涨停" in value or "跌停" in value:
            category = "limit"
        categories.setdefault(category, []).append(value)
    return {key: {"count": len(values), "examples": values[:3]} for key, values in categories.items()}


def _compact_metrics(metrics):
    keys = (
        "initial_capital", "final_equity", "net_profit", "time_weighted_return",
        "annualized_return", "cash_neutral_cumulative_return",
        "cash_neutral_annualized_return", "cash_neutral_twr_cumulative_return",
        "cash_neutral_twr_annualized_return", "cash_neutral_active_calendar_days",
        "cash_neutral_max_drawdown", "annualized_volatility", "max_drawdown", "sharpe_ratio",
        "sortino_ratio", "calmar_ratio", "trade_count", "win_rate", "profit_factor",
    )
    return {key: metrics.get(key) for key in keys if key in metrics}


def _compact_dict(value):
    return {key: value.get(key) for key in ("source_name", "source_version", "skill_name", "skill_version", "data_start", "data_end") if key in value}


def _compact_validation(value):
    split = value.get("sample_split", {}) if isinstance(value, dict) else {}
    return {"sample_split": {key: split.get(key) for key in ("ratio", "train_end", "test_start") if key in split}}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
