"""Normalization and deterministic contracts for user long-term memory."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from mcp_server.domain.identifiers import normalize_ticker


MEMORY_TYPES = {
    "risk_preference",
    "trading_principle",
    "behavioral_habit",
    "process_preference",
    "constraint",
}
SCOPE_TYPES = {"global", "strategy", "instrument"}
SOURCE_KINDS = {
    "user_statement",
    "ai_inference",
    "backtest_analysis",
    "manual_import",
}
TOPIC_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,100}$")
DEFAULT_REVIEW_DAYS = {"risk_preference": 180}


def normalize_memory_candidate(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("memory candidate 必须是对象")
    memory_type = payload.get("memory_type")
    if memory_type not in MEMORY_TYPES:
        raise ValueError("memory_type 不受支持")
    scope_type = payload.get("scope_type", "global")
    if scope_type not in SCOPE_TYPES:
        raise ValueError("scope_type 必须是 global、strategy 或 instrument")
    scope_key = payload.get("scope_key")
    if scope_type == "global":
        scope_key = None
    elif not isinstance(scope_key, str) or not scope_key.strip():
        raise ValueError("非 global 记忆必须提供 scope_key")
    elif scope_type == "instrument":
        code, market = normalize_ticker(scope_key.strip())
        scope_key = "{}{}".format(market, code)
    else:
        scope_key = scope_key.strip()
    topic_key = payload.get("topic_key")
    if not isinstance(topic_key, str) or not TOPIC_PATTERN.fullmatch(topic_key.strip()):
        raise ValueError("topic_key 必须是小写字母开头的稳定键")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content 不能为空")
    if len(content.strip()) > 2000:
        raise ValueError("content 不能超过 2000 个字符")
    structured_value = payload.get("structured_value")
    if structured_value is not None and not isinstance(structured_value, dict):
        raise ValueError("structured_value 必须是对象或 null")
    source = payload.get("source") or {}
    if not isinstance(source, dict):
        raise ValueError("source 必须是对象")
    source_kind = source.get("kind", "user_statement")
    if source_kind not in SOURCE_KINDS:
        raise ValueError("source.kind 不受支持")
    source_summary = source.get("summary", "")
    if not isinstance(source_summary, str) or len(source_summary) > 500:
        raise ValueError("source.summary 不能超过 500 个字符")
    run_ids = source.get("run_ids", [])
    if not isinstance(run_ids, list) or any(not isinstance(item, int) for item in run_ids):
        raise ValueError("source.run_ids 必须是整数数组")
    evidence_refs = source.get("evidence_refs", [])
    if not isinstance(evidence_refs, list) or any(
        not isinstance(item, str) or not item.strip() for item in evidence_refs
    ):
        raise ValueError("source.evidence_refs 必须是字符串数组")
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags):
        raise ValueError("tags 必须是字符串数组")
    review_due_at = payload.get("review_due_at")
    if review_due_at is None and memory_type in DEFAULT_REVIEW_DAYS:
        review_due_at = (
            datetime.now(timezone.utc)
            + timedelta(days=DEFAULT_REVIEW_DAYS[memory_type])
        ).isoformat()
    if review_due_at is not None:
        _parse_datetime(review_due_at, "review_due_at")
    candidate = {
        "schema_version": 1,
        "memory_type": memory_type,
        "scope_type": scope_type,
        "scope_key": scope_key,
        "topic_key": topic_key.strip(),
        "content": content.strip(),
        "structured_value": structured_value,
        "source": {
            "kind": source_kind,
            "summary": source_summary.strip(),
            "run_ids": sorted(set(run_ids)),
            "evidence_refs": sorted(set(evidence_refs)),
        },
        "tags": sorted(set(tag.strip() for tag in tags if tag.strip())),
    }
    if review_due_at is not None:
        candidate["review_due_at"] = str(review_due_at)
    return candidate


def memory_candidate_hash(candidate: Dict[str, Any]) -> str:
    payload = json.dumps(
        normalize_memory_candidate(candidate),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def memory_conflict_key(candidate: Dict[str, Any]):
    normalized = normalize_memory_candidate(candidate)
    return (
        normalized["memory_type"],
        normalized["scope_type"],
        normalized["scope_key"],
        normalized["topic_key"],
    )


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("{} 必须是 ISO 时间字符串".format(field))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("{} 不是有效的 ISO 时间".format(field)) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_review_due(value: Optional[str], now: Optional[datetime] = None) -> bool:
    if not value:
        return False
    current = now or datetime.now(timezone.utc)
    return _parse_datetime(value, "review_due_at") <= current
