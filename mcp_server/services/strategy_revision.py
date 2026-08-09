"""Explicit, reviewable strategy revision diffs."""

import hashlib
import json
from typing import Any, Dict, Optional

from mcp_server.domain.strategy import StrategySpec


ASSUMPTION_FIELDS = (
    "benchmark",
    "risk_free_rate_annual",
    "cost_profile",
    "position_sizing",
    "execution",
    "validation",
)


def prepare_strategy_revision(
    base_strategy: Dict[str, Any],
    proposed_strategy: Dict[str, Any],
    source_run_id: Optional[int] = None,
    change_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = _normalized(base_strategy)
    proposed = _normalized(proposed_strategy)
    changes = []
    _diff(base, proposed, "", changes, change_details or {})
    unchanged = [
        field for field in ASSUMPTION_FIELDS if base.get(field) == proposed.get(field)
    ]
    payload = {"changes": changes, "unchanged_assumptions": unchanged}
    if source_run_id is not None:
        payload["source_run_id"] = int(source_run_id)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["diff_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    payload["base_strategy"] = base
    payload["proposed_strategy"] = proposed
    return payload


def verify_approved_revision(
    store,
    spec: StrategySpec,
    parent_version: str,
    change_set: Any,
    approved_diff_hash: str,
    source_run_id: Optional[int] = None,
) -> Dict[str, Any]:
    parent = store.get_strategy_version(spec.strategy_id, parent_version)
    if parent is None:
        raise ValueError("找不到父策略版本：{}@{}".format(spec.strategy_id, parent_version))
    diff = prepare_strategy_revision(
        parent["strategy"],
        spec.to_dict(),
        source_run_id=source_run_id,
        change_details={
            item.get("path"): item
            for item in (change_set or [])
            if isinstance(item, dict) and item.get("path")
        },
    )
    if diff["diff_hash"] != approved_diff_hash:
        raise ValueError("批准的策略 diff 哈希与实际策略不一致")
    if diff["changes"] != list(change_set or []):
        raise ValueError("批准的策略 diff 与实际策略字段不一致")
    return diff


def _normalized(payload):
    if isinstance(payload, StrategySpec):
        spec = payload
    else:
        spec = StrategySpec.from_dict(dict(payload or {}))
    if not spec.is_valid:
        raise ValueError("策略修订无效：{}".format("；".join(spec.validation_errors)))
    return spec.to_dict()


def _diff(base, proposed, prefix, changes, details):
    keys = sorted(set(base) | set(proposed))
    for key in keys:
        path = "$.{}".format(key) if not prefix else "{}.{}".format(prefix, key)
        old = base.get(key)
        new = proposed.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            _diff(old, new, path, changes, details)
            continue
        if old == new:
            continue
        detail = details.get(path) if isinstance(details, dict) else None
        detail = dict(detail) if isinstance(detail, dict) else {}
        changes.append(
            {
                "path": path,
                "old_value": old,
                "new_value": new,
                "reason": detail.get("reason", "待与用户逐项讨论"),
                "evidence_refs": list(detail.get("evidence_refs") or []),
                "expected_impact": detail.get("expected_impact", "待确认"),
                "risk": detail.get("risk", "待确认"),
            }
        )
