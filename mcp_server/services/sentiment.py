"""Versioned sentiment extraction contracts and deterministic factor aggregation."""

import hashlib
import json
import math
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo


SENTIMENT_PROFILE = "sentiment-baseline-v1"
SENTIMENT_HORIZONS = (1, 5, 20)
SENTIMENT_TZ = ZoneInfo("Asia/Shanghai")
MAX_SUMMARY_CHARS = 2000


def normalize_document(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize document metadata while deliberately dropping full source text."""

    if not isinstance(payload, Mapping):
        raise ValueError("sentiment document must be an object")
    platform = _required_text(payload, "platform")
    source_id = _required_text(payload, "source_id")
    document_type = str(payload.get("document_type") or "news").lower()
    if document_type not in {"news", "blogger"}:
        raise ValueError("document_type must be news or blogger")
    summary = str(payload.get("summary") or payload.get("content") or "").strip()
    if not summary:
        raise ValueError("sentiment document requires summary")
    summary = summary[:MAX_SUMMARY_CHARS]
    published_at = _normalise_datetime(payload.get("published_at"), "published_at")
    collected_at = _normalise_datetime(
        payload.get("collected_at") or datetime.now(SENTIMENT_TZ), "collected_at"
    )
    content = payload.get("content")
    content_hash = str(payload.get("content_hash") or "").strip()
    if content is not None:
        content_hash = content_hash or hashlib.sha256(
            _canonical_text(str(content)).encode("utf-8")
        ).hexdigest()
    if not content_hash:
        raise ValueError("sentiment document requires content_hash or transient content")
    targets = _text_list(payload.get("targets"))
    industries = _text_list(payload.get("shenwan_industries"))
    event_fingerprint = str(payload.get("event_fingerprint") or "").strip()
    if not event_fingerprint:
        event_fingerprint = hashlib.sha256(
            "|".join(
                (
                    platform,
                    document_type,
                    published_at[:10],
                    _canonical_text(summary).lower(),
                    ",".join(targets),
                    ",".join(industries),
                )
            ).encode("utf-8")
        ).hexdigest()
    document_id = str(payload.get("document_id") or "").strip()
    if not document_id:
        document_id = "sentiment-doc-{}".format(
            hashlib.sha256(
                "|".join((platform, source_id, published_at, content_hash)).encode("utf-8")
            ).hexdigest()[:20]
        )
    metadata = dict(payload.get("metadata") or {})
    for key in (
        "data_as_of",
        "source_name",
        "source_url",
        "provider_id",
        "skill_name",
        "skill_version",
        "methodology",
        "error_reason",
    ):
        if payload.get(key) is not None:
            metadata.setdefault(key, payload.get(key))
    return {
        "document_id": document_id,
        "platform": platform,
        "source_id": source_id,
        "author_id": _optional_text(payload.get("author_id")),
        "author_name": _optional_text(payload.get("author_name")),
        "canonical_url": str(payload.get("canonical_url") or "").strip(),
        "published_at": published_at,
        "collected_at": collected_at,
        "content_hash": content_hash,
        "event_fingerprint": event_fingerprint,
        "summary": summary,
        "document_type": document_type,
        "targets": targets,
        "shenwan_industries": industries,
        "concept_tags": _text_list(payload.get("concept_tags")),
        "status": str(payload.get("status") or "collected"),
        "metadata": metadata,
    }


def normalize_extraction(
    document_id: str,
    payload: Mapping[str, Any],
    model: str,
    prompt_version: str,
) -> Dict[str, Any]:
    """Validate the bounded AI output that is allowed to enter the factor layer."""

    if not isinstance(payload, Mapping):
        raise ValueError("sentiment extraction must be an object")
    claims = payload.get("claims") or []
    if not isinstance(claims, list):
        raise ValueError("sentiment extraction claims must be an array")
    normalized_claims = []
    for index, raw in enumerate(claims):
        if not isinstance(raw, Mapping):
            raise ValueError("sentiment claim {} must be an object".format(index))
        direction = _number(raw.get("direction"))
        if direction not in {-1, 0, 1}:
            raise ValueError("sentiment claim direction must be -1, 0, or 1")
        confidence = _bounded_number(raw.get("confidence", 1.0), "confidence")
        relevance = _bounded_number(raw.get("relevance", 1.0), "relevance")
        horizon = raw.get("time_horizon", 5)
        try:
            horizon = int(horizon)
        except (TypeError, ValueError) as exc:
            raise ValueError("sentiment claim time_horizon must be 1, 5, or 20") from exc
        if horizon not in SENTIMENT_HORIZONS:
            raise ValueError("sentiment claim time_horizon must be 1, 5, or 20")
        normalized_claims.append(
            {
                "claim_id": str(raw.get("claim_id") or "claim-{}".format(index + 1)),
                "direction": int(direction),
                "confidence": confidence,
                "relevance": relevance,
                "time_horizon": horizon,
                "event_type": str(raw.get("event_type") or "opinion"),
                "targets": _text_list(raw.get("targets")),
                "shenwan_industries": _text_list(raw.get("shenwan_industries")),
                "concept_tags": _text_list(raw.get("concept_tags")),
                "evidence_refs": _text_list(
                    raw.get("evidence_refs")
                    or ([raw.get("evidence_id")] if raw.get("evidence_id") else [])
                ),
                "strategy_statement": dict(raw.get("strategy_statement") or {})
                if isinstance(raw.get("strategy_statement"), Mapping)
                else None,
            }
        )
    return {
        "document_id": str(document_id),
        "summary": str(payload.get("summary") or "").strip()[:MAX_SUMMARY_CHARS],
        "claims": normalized_claims,
        "evidence_refs": _text_list(
            payload.get("evidence_refs")
            or payload.get("evidence_ids")
            or ([payload.get("evidence_id")] if payload.get("evidence_id") else [])
        ),
        "extraction_model": str(model or "unknown"),
        "prompt_version": str(prompt_version or "unknown"),
        "status": str(payload.get("status") or "accepted"),
    }


def build_extraction_context(documents: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a stable, bounded context hash for Agent extraction approval."""

    bounded = []
    for document in sorted(documents, key=lambda item: str(item.get("document_id", ""))):
        bounded.append(
            {
                key: document.get(key)
                for key in (
                    "document_id",
                    "platform",
                    "source_id",
                    "author_id",
                    "author_name",
                    "canonical_url",
                    "published_at",
                    "collected_at",
                    "content_hash",
                    "event_fingerprint",
                    "summary",
                    "document_type",
                    "targets",
                    "shenwan_industries",
                    "concept_tags",
                )
            }
        )
    payload = json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    evidence_ids = [
        "sentiment:document:{}".format(item["document_id"])
        for item in bounded
        if item.get("document_id")
    ]
    return {
        "documents": bounded,
        "evidence_ids": evidence_ids,
        "context_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def aggregate_sentiment(
    documents: Sequence[Mapping[str, Any]],
    extractions: Sequence[Mapping[str, Any]],
    as_of: Any,
    cutoff: str = "15:00",
    target: Optional[str] = None,
    industry: Optional[str] = None,
    horizon: int = 5,
    author_weights: Optional[Mapping[str, float]] = None,
    personalized_author_weights: Optional[Mapping[str, float]] = None,
    market_confirmation: Optional[Mapping[str, Any]] = None,
    trading_dates: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Aggregate only information available at a fixed cut-off time."""

    if horizon not in SENTIMENT_HORIZONS:
        raise ValueError("sentiment horizon must be 1, 5, or 20")
    as_of_date = _as_date(as_of)
    cutoff_time = time.fromisoformat(str(cutoff))
    by_id = {str(item.get("document_id")): item for item in documents}
    extraction_by_id = {
        str(item.get("document_id")): item for item in extractions
    }
    candidates = []
    for document_id, extraction in extraction_by_id.items():
        document = by_id.get(document_id)
        if document is None:
            continue
        if not _available_by_cutoff(document, as_of_date, cutoff_time):
            continue
        for claim in extraction.get("claims", []) or []:
            if int(claim.get("time_horizon", 5)) != horizon:
                continue
            if not _matches_scope(document, claim, target, industry):
                continue
            age = _age_days(document.get("published_at"), as_of_date, trading_dates)
            freshness = math.pow(0.5, max(0, age) / float(horizon))
            base_weight = (
                float(claim.get("confidence", 1.0))
                * float(claim.get("relevance", 1.0))
                * freshness
            )
            candidates.append(
                {
                    "document": document,
                    "claim": claim,
                    "direction": int(claim["direction"]),
                    "base_weight": base_weight,
                }
            )

    news = [item for item in candidates if item["document"].get("document_type") == "news"]
    bloggers = [
        item for item in candidates if item["document"].get("document_type") == "blogger"
    ]
    result = {
        "news_event_sentiment": _weighted_factor(news),
        "blogger_consensus_equal": _weighted_factor(bloggers),
        "blogger_consensus_performance_weighted": _weighted_factor(
            bloggers, author_weights, horizon
        ),
        "opinion_divergence": _divergence(bloggers),
        "attention_heat": _attention(candidates),
        "industry_sentiment": _weighted_factor(
            [item for item in candidates if industry and industry in (
                item["claim"].get("shenwan_industries") or item["document"].get("shenwan_industries") or []
            )]
        ),
        "market_confirmation": dict(market_confirmation)
        if market_confirmation is not None
        else _missing("market confirmation not supplied"),
        "personalized_sentiment": _weighted_factor(
            bloggers, personalized_author_weights, horizon
        ),
    }
    for item in result.values():
        item.update({"horizon": horizon, "cutoff": cutoff, "data_as_of": str(as_of_date)})
    return result


def rolling_percentile(value: Optional[float], history: Sequence[float], minimum: int = 20):
    """Return a bounded percentile or None when the history is too short."""

    if value is None or len(history) < minimum:
        return None
    values = [float(item) for item in history if item is not None]
    if len(values) < minimum:
        return None
    return (sum(item <= float(value) for item in values)) / float(len(values))


def add_percentiles(
    factors: Mapping[str, Mapping[str, Any]], history: Mapping[str, Sequence[float]]
) -> Dict[str, Dict[str, Any]]:
    result = {key: dict(value) for key, value in factors.items()}
    for key, item in result.items():
        item["percentile"] = rolling_percentile(
            item.get("value"), history.get(key, [])
        )
        if item.get("value") is not None and item["percentile"] is None:
            item["percentile_status"] = "insufficient_history"
    return result


def build_sentiment_snapshot(
    documents: Sequence[Mapping[str, Any]],
    extractions: Sequence[Mapping[str, Any]],
    snapshot_date: Any,
    cutoff: str,
    scope_type: str,
    scope_key: Optional[str] = None,
    trading_dates: Optional[Sequence[str]] = None,
    history: Optional[Mapping[str, Sequence[float]]] = None,
    author_weights: Optional[Mapping[str, float]] = None,
    personalized_author_weights: Optional[Mapping[str, float]] = None,
    market_confirmation: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one immutable snapshot containing the three standard horizons."""

    if scope_type not in {"market", "instrument", "industry"}:
        raise ValueError("sentiment scope_type must be market, instrument, or industry")
    as_of_date = _as_date(snapshot_date)
    target = scope_key if scope_type == "instrument" else None
    industry = scope_key if scope_type == "industry" else None
    factor_sets = {}
    evidence = []
    for horizon in SENTIMENT_HORIZONS:
        factors = aggregate_sentiment(
            documents,
            extractions,
            as_of=as_of_date,
            cutoff=cutoff,
            target=target,
            industry=industry,
            horizon=horizon,
            author_weights=author_weights,
            personalized_author_weights=personalized_author_weights,
            market_confirmation=market_confirmation,
            trading_dates=trading_dates,
        )
        factor_sets["{}d".format(horizon)] = add_percentiles(
            factors, _history_for_horizon(history or {}, horizon)
        )
        for factor_name, factor in factors.items():
            for document_id in factor.get("contributors", []) or []:
                evidence.append(
                    {
                        "evidence_id": "sentiment:{}:{}:{}".format(
                            horizon, factor_name, document_id
                        ),
                        "document_id": document_id,
                        "factor": factor_name,
                        "horizon": horizon,
                    }
                )
    valid_dates = {
        str(document.get("published_at", ""))[:10]
        for document in documents
        if _available_by_cutoff(document, as_of_date, time.fromisoformat(cutoff))
        and any(
            str(extraction.get("document_id")) == str(document.get("document_id"))
            for extraction in extractions
        )
    }
    all_trading_dates = {str(item)[:10] for item in (trading_dates or [])}
    coverage = (
        len(valid_dates & all_trading_dates) / float(len(all_trading_dates))
        if all_trading_dates
        else None
    )
    valid_snapshot_count = len(valid_dates)
    eligible = bool(
        coverage is not None and coverage >= 0.5 and valid_snapshot_count >= 20
    )
    eligibility = {
        "status": "formal" if eligible else "exploratory_only",
        "eligible": eligible,
        "coverage": coverage,
        "valid_snapshot_count": valid_snapshot_count,
        "minimum_coverage": 0.5,
        "minimum_valid_snapshots": 20,
        "reason": None if eligible else "sentiment history coverage or sample is insufficient",
    }
    source_counts = {}
    for document in documents:
        platform = str(document.get("platform") or "unknown")
        source_counts[platform] = source_counts.get(platform, 0) + 1
    industries = sorted(
        {
            str(industry)
            for document in documents
            for industry in document.get("shenwan_industries", []) or []
        }
    )
    industry_attribution = {}
    for industry_name in industries:
        industry_factors = aggregate_sentiment(
            documents,
            extractions,
            as_of=as_of_date,
            cutoff=cutoff,
            industry=industry_name,
            horizon=5,
            author_weights=author_weights,
            personalized_author_weights=personalized_author_weights,
            market_confirmation=market_confirmation,
        )
        industry_attribution[industry_name] = industry_factors["industry_sentiment"].get("value")
    provenance = []
    for document in documents:
        metadata = document.get("metadata") or {}
        item = {
            "provider_id": metadata.get("provider_id"),
            "skill_name": metadata.get("skill_name"),
            "skill_version": metadata.get("skill_version"),
            "source_name": metadata.get("source_name"),
            "source_url": metadata.get("source_url"),
        }
        if item not in provenance:
            provenance.append(item)
    return {
        "schema_version": 1,
        "profile": SENTIMENT_PROFILE,
        "snapshot_date": str(as_of_date),
        "cutoff": cutoff,
        "scope": {"type": scope_type, "key": scope_key},
        "factors": factor_sets,
        "backtest_eligibility": eligibility,
        "backtest_gate": eligibility,
        "coverage": eligibility,
        "source_counts": source_counts,
        "industry_attribution": industry_attribution,
        "provenance": {"providers": provenance, "document_count": len(documents)},
        "status": "completed",
        "evidence": _unique_evidence(evidence),
        "warnings": [] if eligible else ["sentiment factor coverage is below formal backtest gate"],
        "generated_at": datetime.now(SENTIMENT_TZ).isoformat(),
    }


def evaluate_author_performance(
    documents: Sequence[Mapping[str, Any]],
    extractions: Sequence[Mapping[str, Any]],
    returns_by_target: Mapping[str, Mapping[str, float]],
    benchmark_returns_by_target: Mapping[str, Mapping[str, float]],
    as_of: Any,
) -> List[Dict[str, Any]]:
    """Evaluate only completed forward windows and shrink small samples to neutral."""

    cutoff_date = _as_date(as_of)
    grouped: Dict[tuple, List[Dict[str, float]]] = {}
    documents_by_id = {str(item.get("document_id")): item for item in documents}
    for extraction in extractions:
        document = documents_by_id.get(str(extraction.get("document_id")))
        if document is None or not document.get("author_id"):
            continue
        published = _parse_datetime(document.get("published_at")).date()
        for claim in extraction.get("claims", []) or []:
            horizon = int(claim.get("time_horizon", 5))
            targets = claim.get("targets") or document.get("targets") or []
            for target in targets:
                actual_series = returns_by_target.get(str(target)) or {}
                benchmark_series = benchmark_returns_by_target.get(str(target)) or {}
                dates = sorted(
                    str(day)[:10]
                    for day in actual_series
                    if str(day)[:10] > str(published) and str(day)[:10] <= str(cutoff_date)
                )
                if len(dates) < horizon:
                    continue
                end = dates[horizon - 1]
                actual = float(actual_series[end])
                benchmark = benchmark_series.get(end)
                if benchmark is None:
                    continue
                excess = actual - float(benchmark)
                direction = int(claim.get("direction", 0))
                key = (str(document["author_id"]), horizon)
                grouped.setdefault(key, []).append(
                    {
                        "absolute_return": actual,
                        "excess_return": excess,
                        "hit": 1.0 if direction == 0 or direction * excess > 0 else 0.0,
                    }
                )
    results = []
    authors = {
        (str(document.get("author_id")), int(claim.get("time_horizon", 5)))
        for extraction in extractions
        for document in documents_by_id.values()
        if str(extraction.get("document_id")) == str(document.get("document_id"))
        for claim in extraction.get("claims", []) or []
        if document.get("author_id")
    }
    for author_id, horizon in sorted(authors):
        rows = grouped.get((author_id, horizon), [])
        sample_count = len(rows)
        hit_rate = sum(row["hit"] for row in rows) / sample_count if sample_count else None
        absolute = (
            sum(row["absolute_return"] for row in rows) / sample_count
            if sample_count
            else None
        )
        excess = (
            sum(row["excess_return"] for row in rows) / sample_count
            if sample_count
            else None
        )
        if sample_count < 20 or hit_rate is None or excess is None:
            weight = 1.0
            status = "insufficient_sample"
        else:
            score = 0.5 * (2.0 * hit_rate - 1.0) + 0.5 * math.tanh(excess * 10.0)
            weight = min(1.5, max(0.5, 1.0 + 0.5 * score))
            status = "ok"
        results.append(
            {
                "author_id": author_id,
                "horizon": horizon,
                "as_of": str(cutoff_date),
                "sample_count": sample_count,
                "hit_rate": hit_rate,
                "mean_absolute_return": absolute,
                "mean_excess_return": excess,
                "weight": weight,
                "status": status,
            }
        )
    return results


def _weighted_factor(items, weights=None, horizon=None):
    if not items:
        return _missing("no eligible extracted claims")
    numer = 0.0
    denom = 0.0
    contributors = []
    for item in items:
        author_id = item["document"].get("author_id")
        multiplier = 1.0 if weights is None else _author_weight(weights, author_id, horizon)
        multiplier = min(1.5, max(0.5, multiplier))
        weight = item["base_weight"] * multiplier
        numer += item["direction"] * weight
        denom += weight
        contributors.append(item["document"].get("document_id"))
    if denom <= 0:
        return _missing("eligible claim weights sum to zero")
    return {
        "status": "ok",
        "value": round(100.0 * numer / denom, 8),
        "raw_value": round(numer / denom, 8),
        "count": len(items),
        "sample_count": len(items),
        "contributors": sorted(set(contributors)),
    }


def _history_for_horizon(history, horizon):
    """Accept both the legacy flat history shape and per-horizon histories."""

    if not isinstance(history, Mapping):
        return {}
    nested = history.get("{}d".format(horizon), history.get(str(horizon)))
    return nested if isinstance(nested, Mapping) else history


def _author_weight(weights, author_id, horizon):
    value = weights.get(author_id, 1.0)
    if isinstance(value, Mapping):
        value = value.get(str(horizon), value.get(horizon, 1.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _unique_evidence(items):
    unique = {}
    for item in items:
        unique[item["evidence_id"]] = item
    return list(unique.values())


def _divergence(items):
    if not items:
        return _missing("no eligible blogger claims")
    factor = _weighted_factor(items)
    mean = float(factor["raw_value"])
    total = sum(item["base_weight"] for item in items)
    if total <= 0:
        return _missing("blogger claim weights sum to zero")
    variance = sum(
        item["base_weight"] * (item["direction"] - mean) ** 2 for item in items
    ) / total
    return {
        "status": "ok",
        "value": round(min(100.0, math.sqrt(max(0.0, variance)) * 100.0), 8),
        "raw_value": round(math.sqrt(max(0.0, variance)), 8),
        "count": len(items),
        "sample_count": len(items),
        "contributors": factor["contributors"],
    }


def _attention(items):
    if not items:
        return _missing("no eligible claims")
    return {
        "status": "ok",
        "value": round(min(100.0, 100.0 * math.log1p(len(items)) / math.log1p(20)), 8),
        "raw_value": len(items),
        "count": len(items),
        "sample_count": len(items),
        "contributors": sorted(
            {item["document"].get("document_id") for item in items}
        ),
    }


def _missing(reason):
    return {
        "status": "missing",
        "value": None,
        "raw_value": None,
        "count": 0,
        "sample_count": 0,
        "reason": reason,
    }


def _matches_scope(document, claim, target, industry):
    if target:
        targets = set(document.get("targets") or []) | set(claim.get("targets") or [])
        if target not in targets:
            return False
    if industry:
        industries = set(document.get("shenwan_industries") or []) | set(
            claim.get("shenwan_industries") or []
        )
        if industry not in industries:
            return False
    return True


def _available_by_cutoff(document, as_of, cutoff_time):
    published = _parse_datetime(document.get("published_at"))
    collected = _parse_datetime(document.get("collected_at"))
    cutoff = datetime.combine(as_of, cutoff_time, tzinfo=SENTIMENT_TZ)
    return published <= cutoff and collected <= cutoff


def _age_days(value, as_of, trading_dates=None):
    return _age_days_with_calendar(value, as_of, trading_dates)


def _age_days_with_calendar(value, as_of, trading_dates):
    try:
        published = _parse_datetime(value).date()
        if trading_dates:
            return sum(
                1
                for day in trading_dates
                if published < _as_date(day) <= as_of
            )
        return max(0, (as_of - published).days)
    except (TypeError, ValueError):
        return 0


def _normalise_datetime(value, field):
    try:
        return _parse_datetime(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("{} must be an ISO datetime".format(field)) from exc


def _parse_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("datetime is required")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SENTIMENT_TZ)
    return parsed.astimezone(SENTIMENT_TZ)


def _as_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _required_text(payload, key):
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError("sentiment document requires {}".format(key))
    return value


def _optional_text(value):
    text = str(value or "").strip()
    return text or None


def _text_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("sentiment tags must be an array")
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _bounded_number(value, name):
    number = _number(value)
    if number is None or number < 0 or number > 1:
        raise ValueError("sentiment claim {} must be between 0 and 1".format(name))
    return float(number)


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _canonical_text(value):
    return re.sub(r"\s+", " ", str(value)).strip()
