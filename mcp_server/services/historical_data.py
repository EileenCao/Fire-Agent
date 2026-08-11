"""Historical daily-bar preparation backed by the a-stock-data routes."""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from mcp_server.domain.identifiers import normalize_ticker
from mcp_server.services.data_cache import ParquetDataCache, ParquetDataCacheError
from mcp_server.workspace import Workspace


SOURCE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_DEFAULT_CACHE = object()


class HistoricalDataError(RuntimeError):
    """Raised when the configured historical data route cannot be used."""


@dataclass
class HistoricalDataResult:
    data: Dict[str, List[Dict[str, Any]]]
    provenance: Dict[str, Any]
    missing_symbols: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)
    cache_paths: Dict[str, str] = field(default_factory=dict)


class WorkspaceHistoricalDataProvider:
    """Fetch, normalize and cache daily bars in the user workspace.

    ``fetcher`` is intentionally injectable. Tests and offline replays can use a
    deterministic provider while production uses the Tencent front-adjusted K-line
    route documented as an a-stock-data fallback. Calls are always serial.
    """

    def __init__(
        self,
        workspace: Workspace,
        skill,
        fetcher: Optional[Callable[..., Iterable[Any]]] = None,
        cache=_DEFAULT_CACHE,
    ):
        self.workspace = workspace
        self.skill = skill
        self.fetcher = fetcher or AStockDailyBarsFetcher()
        self.cache = (
            ParquetDataCache(workspace.parquet_dir)
            if cache is _DEFAULT_CACHE
            else cache
        )

    def fetch(
        self,
        codes: Iterable[str],
        start_date: Any,
        end_date: Any,
    ) -> HistoricalDataResult:
        start = _as_date(start_date)
        end = _as_date(end_date)
        if start > end:
            raise ValueError("历史数据开始日期不能晚于结束日期")

        normalized_codes = _normalize_codes(codes)
        data: Dict[str, List[Dict[str, Any]]] = {}
        missing_symbols: List[str] = []
        errors: Dict[str, str] = {}
        cache_paths: Dict[str, str] = {}
        per_symbol: Dict[str, Dict[str, Any]] = {}

        for code, market in normalized_codes:
            source_version = "a-stock-data:{}".format(self.skill.version)
            cached = self._read_cached(code, start, end, source_version)
            if cached is not None:
                bars, metadata, cache_path = cached
                data[code] = bars
                cache_paths[code] = str(cache_path)
                per_symbol[code] = {
                    **metadata,
                    "code": code,
                    "cache_hit": True,
                    "data_start": str(bars[0]["date"]),
                    "data_end": str(bars[-1]["date"]),
                    "bar_count": len(bars),
                }
                continue
            try:
                raw_bars = list(self.fetcher(code, market, start, end))
                bars = _normalize_bars(raw_bars, start, end)
                if not bars:
                    raise HistoricalDataError("数据源未返回指定区间内的完整日线")
            except Exception as exc:
                missing_symbols.append(code)
                errors[code] = str(exc)
                continue

            symbol_provenance = self._symbol_provenance(code, start, end, bars)
            self._write_raw(code, raw_bars, symbol_provenance)
            if self.cache is not None:
                try:
                    cache_paths[code] = str(
                        self.cache.write(code, bars, symbol_provenance)
                    )
                except (ParquetDataCacheError, OSError, ValueError) as exc:
                    errors[code] = "Parquet 缓存未写入：{}".format(exc)
            data[code] = bars
            per_symbol[code] = symbol_provenance

        dates = [
            str(bar["date"])
            for bars in data.values()
            for bar in bars
        ]
        provenance = {
            "source_name": "腾讯财经（a-stock-data K 线路由）",
            "source_url": SOURCE_URL,
            "source_version": "a-stock-data:{}".format(self.skill.version),
            "skill_name": self.skill.name,
            "skill_version": self.skill.version,
            "route": "tencent_fqkline_qfq",
            "price_basis": "adjusted",
            "adjustment_method": "前复权（qfq）",
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "data_start": min(dates) if dates else None,
            "data_end": max(dates) if dates else None,
            "collected_at": datetime.now().astimezone().isoformat(),
            "missing_symbols": list(missing_symbols),
            "errors": dict(errors),
            "cache_paths": dict(cache_paths),
            "per_symbol": per_symbol,
        }
        return HistoricalDataResult(
            data=data,
            provenance=provenance,
            missing_symbols=missing_symbols,
            errors=errors,
            cache_paths=cache_paths,
        )

    def _read_cached(
        self,
        code: str,
        start: date,
        end: date,
        source_version: str,
    ):
        if self.cache is None or not hasattr(self.cache, "read_metadata"):
            return None
        try:
            metadata = self.cache.read_metadata(code, source_version)
            requested_start = _as_date(metadata.get("requested_start"))
            requested_end = _as_date(metadata.get("requested_end"))
            if requested_start > start or requested_end < end:
                return None
            bars = _normalize_bars(self.cache.read(code, source_version), start, end)
            if not bars:
                return None
            path = self.cache.path_for(code, source_version)
            return bars, metadata, path
        except (KeyError, TypeError, ValueError, OSError, ParquetDataCacheError):
            return None

    def _symbol_provenance(
        self,
        code: str,
        start: date,
        end: date,
        bars: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "source_name": "腾讯财经（a-stock-data K 线路由）",
            "source_url": SOURCE_URL,
            "source_version": "a-stock-data:{}".format(self.skill.version),
            "skill_name": self.skill.name,
            "skill_version": self.skill.version,
            "code": code,
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "data_start": str(bars[0]["date"]),
            "data_end": str(bars[-1]["date"]),
            "bar_count": len(bars),
            "route": "tencent_fqkline_qfq",
            "price_basis": "adjusted",
            "adjustment_method": "前复权（qfq）",
        }

    def _write_raw(
        self,
        code: str,
        bars: List[Any],
        provenance: Dict[str, Any],
    ) -> None:
        path = self.workspace.raw_dir / (code + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"code": code, "bars": bars, "provenance": provenance},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )


class AStockDailyBarsFetcher:
    """Serial Tencent front-adjusted daily-bar fetcher.

    Tencent is the approved non-Eastmoney K-line route in a-stock-data's
    fallback table. The response is converted to the engine's stable field names.
    """

    def __init__(
        self,
        session=None,
        timeout: int = 20,
        segment_days: int = 365,
        max_rows: int = 640,
        bypass_proxy: bool = True,
    ):
        if session is None:
            try:
                import requests

                session = requests.Session()
                session.headers.update({"User-Agent": "Mozilla/5.0"})
            except ImportError as exc:
                raise HistoricalDataError(
                    "自动获取历史数据需要 requests；请按 a-stock-data Skill 安装依赖"
                ) from exc
        self.session = session
        self.session.trust_env = not bypass_proxy
        self.timeout = timeout
        if segment_days < 1:
            raise ValueError("Tencent historical segment_days must be positive")
        self.segment_days = int(segment_days)
        self.max_rows = min(max(1, int(max_rows)), 640)

    def __call__(
        self,
        code: str,
        market: str,
        start_date: date,
        end_date: date,
    ) -> List[Dict[str, Any]]:
        start = _as_date(start_date)
        end = _as_date(end_date)
        if start > end:
            raise ValueError("Tencent historical start date must not be after end date")

        merged: Dict[str, Dict[str, Any]] = {}
        symbol = market.lower() + code
        for segment_start, segment_end in _date_segments(
            start, end, self.segment_days
        ):
            params = {
                "param": "{},{},{},{},{},qfq".format(
                    symbol,
                    "day",
                    segment_start.isoformat(),
                    segment_end.isoformat(),
                    self.max_rows,
                )
            }
            response = self.session.get(
                SOURCE_URL, params=params, timeout=self.timeout
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json()
            node = (payload.get("data") or {}).get(symbol) or {}
            rows = node.get("qfqday") or node.get("day") or []
            for row in rows:
                bar = _row_to_bar(row)
                day = _as_date(str(bar["date"])[:10])
                if start <= day <= end:
                    merged[day.isoformat()] = bar

        if not merged:
            raise HistoricalDataError("腾讯前复权日线返回为空：{}".format(code))
        return [merged[key] for key in sorted(merged)]


def resolve_strategy_window(spec, today: Optional[date] = None) -> Tuple[date, date]:
    """Resolve the automatic-fetch window from the strategy validation section."""

    validation = spec.validation or {}
    end = _as_date(validation.get("end_date")) if validation.get("end_date") else (today or date.today())
    start = (
        _as_date(validation["start_date"])
        if validation.get("start_date")
        else end - timedelta(days=365 * 4)
    )
    if start > end:
        raise ValueError("策略 validation.start_date 不能晚于 end_date")
    return start, end


def attach_data_provenance(payload: Dict[str, Any], result: HistoricalDataResult) -> None:
    """Attach automatic-fetch metadata to the immutable strategy payload."""

    policy = dict(payload.get("data_policy") or {})
    policy.update(result.provenance)
    policy["missing_symbols"] = list(result.missing_symbols)
    policy["errors"] = dict(result.errors)
    policy["cache_paths"] = dict(result.cache_paths)
    payload["data_policy"] = policy


def _normalize_codes(codes: Iterable[str]) -> List[Tuple[str, str]]:
    result = []
    seen = set()
    for raw_code in codes:
        code, market = normalize_ticker(str(raw_code))
        if code in seen:
            continue
        seen.add(code)
        result.append((code, market))
    return result


def _normalize_bars(
    rows: Iterable[Any], start: date, end: date
) -> List[Dict[str, Any]]:
    normalized = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        raw_date = raw.get("date") or raw.get("datetime") or raw.get("time")
        if raw_date is None:
            continue
        try:
            day = _as_date(str(raw_date)[:10])
        except (TypeError, ValueError):
            continue
        if day < start or day > end:
            continue
        bar = dict(raw)
        bar["date"] = day.isoformat()
        for field_name in ("open", "high", "low", "close"):
            value = _number(raw.get("adj_" + field_name, raw.get(field_name)))
            if value is None:
                break
            bar[field_name] = value
            bar["adj_" + field_name] = value
        else:
            for field_name in ("volume", "amount"):
                value = _number(raw.get(field_name, raw.get("vol") if field_name == "volume" else None))
                if value is not None:
                    bar[field_name] = value
            normalized.append(bar)
    normalized.sort(key=lambda item: item["date"])
    return normalized


def _row_to_bar(row: Any) -> Dict[str, Any]:
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        raise HistoricalDataError("腾讯日线返回了无法解析的记录")
    values = {
        "date": row[0],
        "open": _number(row[1]),
        "close": _number(row[2]),
        "high": _number(row[3]),
        "low": _number(row[4]),
    }
    if len(row) > 5:
        values["volume"] = _number(row[5])
    if len(row) > 6:
        values["amount"] = _number(row[6])
    if any(values[field] is None for field in ("open", "close", "high", "low")):
        raise HistoricalDataError("腾讯日线存在无法解析的价格字段")
    return values


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _date_segments(start: date, end: date, segment_days: int):
    current = start
    while current <= end:
        segment_end = min(end, current + timedelta(days=segment_days - 1))
        yield current, segment_end
        current = segment_end + timedelta(days=1)


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "", "--", "-") else None
    except (TypeError, ValueError):
        return None
