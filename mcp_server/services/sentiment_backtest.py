"""Date alignment between immutable sentiment snapshots and daily bars."""

from datetime import date
from typing import Any, Dict, Iterable, Mapping, Sequence


def attach_sentiment_snapshots(
    data: Mapping[str, Sequence[Mapping[str, Any]]],
    snapshots: Iterable[Mapping[str, Any]],
) -> Dict[str, list]:
    """Attach the latest not-future snapshot to each bar.

    A bar receives all three horizons.  The indicator layer selects the
    strategy's declared horizon, so a 1/5/20-day strategy cannot accidentally
    read a different horizon from the same field.
    """

    grouped: Dict[tuple, list] = {}
    for snapshot in snapshots:
        scope = snapshot.get("scope") or {}
        scope_type = str(scope.get("type") or "")
        scope_key = str(scope.get("key") or "*")
        if scope_type not in {"market", "instrument", "industry"}:
            continue
        grouped.setdefault((scope_type, scope_key), []).append(snapshot)
    for values in grouped.values():
        values.sort(key=lambda item: (_date_key(item.get("snapshot_date")), str(item.get("cutoff") or "15:00")))

    result: Dict[str, list] = {}
    for code, bars in data.items():
        copied_bars = []
        for bar in bars:
            copied = dict(bar)
            day = _date_key(bar.get("date"))
            merged: Dict[str, Dict[str, Any]] = {}
            candidates = []
            candidates.extend(grouped.get(("market", "*"), []))
            candidates.extend(grouped.get(("market", ""), []))
            candidates.extend(grouped.get(("instrument", str(code)), []))
            candidates.extend(grouped.get(("instrument", str(code).zfill(6)), []))
            industry = bar.get("shenwan_industry") or bar.get("industry")
            if industry:
                candidates.extend(grouped.get(("industry", str(industry)), []))
            selected = _latest_before(candidates, day)
            if selected:
                merged = _merge_snapshot_factors(merged, selected.get("factors") or {})
            copied["sentiment_factors"] = merged
            copied_bars.append(copied)
        result[str(code)] = copied_bars
    return result


def _merge_snapshot_factors(target, factor_sets):
    for horizon_name, factors in factor_sets.items():
        horizon = str(horizon_name).rstrip("d")
        for factor, value in (factors or {}).items():
            if not isinstance(value, Mapping):
                value = {"value": value}
            current = target.setdefault(factor, {})
            horizons = current.setdefault("horizons", {})
            horizons[horizon] = dict(value)
            if horizon == "5" or "value" not in current:
                current.update(dict(value))
    return target


def _latest_before(snapshots, day):
    eligible = [snapshot for snapshot in snapshots if _date_key(snapshot.get("snapshot_date")) <= day]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (_date_key(item.get("snapshot_date")), str(item.get("cutoff") or "15:00")))


def _date_key(value):
    return date.fromisoformat(str(value or "1900-01-01")[:10])
