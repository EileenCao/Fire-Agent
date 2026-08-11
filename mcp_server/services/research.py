"""Deterministic single-instrument research assembly and scoring."""

import hashlib
import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence
from zoneinfo import ZoneInfo

from mcp_server.domain.identifiers import normalize_ticker


RESEARCH_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_PROFILE = "baseline-v1"
DEFAULT_SECTIONS = (
    "market",
    "bars",
    "fundamentals",
    "valuation",
    "capital",
    "news",
    "announcements",
    "relative",
)


class InstrumentDataProvider(Protocol):
    """Provider boundary; implementations must return section envelopes."""

    provider_id: str
    skill_name: str
    skill_version: str

    def collect(
        self,
        instrument: Mapping[str, Any],
        sections: Sequence[str],
        as_of: Optional[date] = None,
        refresh: bool = False,
    ) -> Mapping[str, Mapping[str, Any]]:
        ...


class InstrumentResearchService:
    """Build a reproducible evidence bundle from one injected provider."""

    def __init__(self, provider: InstrumentDataProvider):
        self.provider = provider

    def build(
        self,
        code: str,
        market: Optional[str] = None,
        instrument_type: Optional[str] = None,
        name: Optional[str] = None,
        as_of: Optional[date] = None,
        sections: Optional[Iterable[str]] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        normalized_code, normalized_market = normalize_ticker(code, market)
        resolved_type = _resolve_instrument_type(normalized_code, instrument_type)
        requested_sections = _normalize_sections(sections)
        instrument = {
            "code": normalized_code,
            "market": normalized_market,
            "instrument_type": resolved_type,
            "name": name,
        }
        collected = self.provider.collect(
            instrument, requested_sections, as_of=as_of, refresh=refresh
        ) or {}
        sections_payload = {
            section: _section_envelope(collected.get(section))
            for section in requested_sections
        }
        market_data = sections_payload["market"]["data"]
        resolved_name = market_data.get("name") or name or "未命名标的"
        instrument["name"] = resolved_name

        bars = sections_payload["bars"]["data"]
        technical = build_technical_snapshot(bars)
        technical["provenance"] = dict(
            sections_payload["bars"].get("provenance", {}) or {}
        )
        valuation = _merge_dict(
            sections_payload["valuation"]["data"],
            _market_valuation(market_data),
        )
        sections_payload["valuation"]["data"] = valuation
        scores = score_instrument(
            resolved_type,
            valuation=valuation,
            fundamentals=sections_payload["fundamentals"]["data"],
            technical=technical,
            capital=sections_payload["capital"]["data"],
        )

        warnings = []
        for section in requested_sections:
            envelope = sections_payload[section]
            if envelope["status"] in {"missing", "error"}:
                reason = envelope.get("error_reason") or "未返回可用数据"
                warnings.append("{}：{}".format(section, reason))
        if technical["status"] != "ok":
            warnings.append("bars：无法计算技术指标：{}".format(technical["error_reason"]))

        provenance = _build_provenance(self.provider, sections_payload)
        result = {
            "schema_version": 1,
            "profile": DEFAULT_PROFILE,
            "instrument": instrument,
            "requested_sections": list(requested_sections),
            "market": sections_payload["market"],
            "technical": technical,
            "valuation": sections_payload["valuation"],
            "sections": sections_payload,
            "scores": scores,
            "provenance": provenance,
            "warnings": warnings,
            "errors": [warning for warning in warnings if "error" in warning.lower()],
            "evidence": [],
            "generated_at": datetime.now(RESEARCH_TZ).isoformat(),
            "data_as_of": _latest_as_of(sections_payload),
        }
        result["evidence"] = build_evidence(result)
        return result


def build_technical_snapshot(bars: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    normalized = sorted(
        [bar for bar in bars if _number(bar.get("close")) is not None],
        key=lambda item: str(item.get("date", "")),
    )
    if not normalized:
        return {
            "status": "missing",
            "data_as_of": None,
            "indicators": {},
            "error_reason": "没有可用收盘价",
        }
    closes = [float(bar["close"]) for bar in normalized]
    highs = [_number(bar.get("high")) for bar in normalized]
    lows = [_number(bar.get("low")) for bar in normalized]
    previous = [None] + closes[:-1]
    returns = [
        close / prior - 1.0
        for close, prior in zip(closes, previous)
        if prior not in (None, 0)
    ]
    volumes = [_number(bar.get("volume")) for bar in normalized]
    valid_volumes = [value for value in volumes if value is not None]
    amounts = [_number(bar.get("amount")) for bar in normalized]
    valid_amounts = [value for value in amounts if value is not None]
    average_volume20 = _rolling_mean(valid_volumes, 20)
    latest_volume = valid_volumes[-1] if valid_volumes else None
    latest = closes[-1]
    indicators = {
        "latest_close": latest,
        "return_20d": (
            latest / closes[-21] - 1.0 if len(closes) >= 21 and closes[-21] else None
        ),
        "latest_volume": latest_volume,
        "average_volume20": average_volume20,
        "volume_ratio20": (
            latest_volume / average_volume20
            if latest_volume is not None and average_volume20 not in (None, 0)
            else None
        ),
        "latest_amount": valid_amounts[-1] if valid_amounts else None,
        "average_amount20": _rolling_mean(valid_amounts, 20),
        "ma5": _rolling_mean(closes, 5),
        "ma20": _rolling_mean(closes, 20),
        "ma60": _rolling_mean(closes, 60),
        "ma250": _rolling_mean(closes, 250),
        "rsi14": _rsi(closes, 14),
        "macd": _macd(closes),
        "bollinger20": _bollinger(closes, 20),
        "atr14": _atr(normalized, 14),
        "annualized_volatility20": _annualized_volatility(returns[-20:]),
        "max_drawdown": _max_drawdown(closes),
        "bar_count": len(normalized),
        "latest_date": str(normalized[-1].get("date")),
    }
    return {
        "status": "ok",
        "data_as_of": str(normalized[-1].get("date")),
        "indicators": indicators,
        "methodology": {
            "price_basis": "provider bars",
            "annualization_days": 252,
            "lookbacks": {
                "rsi": 14,
                "bollinger": 20,
                "atr": 14,
                "volume": 20,
            },
        },
    }


def score_instrument(
    instrument_type: str,
    valuation: Mapping[str, Any],
    fundamentals: Mapping[str, Any],
    technical: Mapping[str, Any],
    capital: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a versioned, explainable score; missing data is never zero."""

    indicators = technical.get("indicators", {})
    dimensions = {
        "valuation": _valuation_score(valuation),
        "quality": _quality_score(instrument_type, fundamentals),
        "growth": _growth_score(instrument_type, fundamentals, capital),
        "dividend": _dividend_score(fundamentals),
        "trend_risk": _trend_score(indicators),
    }
    weights = {
        "valuation": 0.20,
        "quality": 0.20,
        "growth": 0.20,
        "dividend": 0.15,
        "trend_risk": 0.25,
    }
    for name, item in dimensions.items():
        item["weight"] = weights[name]
        item.setdefault("raw_values", [])
        item["missing"] = item["score"] is None
    applicable = [
        (name, item) for name, item in dimensions.items() if item["applicable"]
    ]
    scored = [
        (name, item) for name, item in applicable if item["score"] is not None
    ]
    applicable_weight = sum(weights[name] for name, _ in applicable)
    scored_weight = sum(weights[name] for name, _ in scored)
    coverage = scored_weight / applicable_weight if applicable_weight else 0.0
    overall = (
        sum(item["score"] * weights[name] for name, item in scored) / scored_weight
        if scored_weight
        else None
    )
    if len(scored) < 3 or coverage < 0.6:
        status = "insufficient_evidence"
    elif overall is not None and overall >= 60:
        status = "continue_research"
    else:
        status = "watch"
    return {
        "profile": DEFAULT_PROFILE,
        "dimensions": dimensions,
        "weights": weights,
        "overall": round(overall, 4) if overall is not None else None,
        "coverage": round(coverage, 4),
        "valid_dimensions": len(scored),
        "applicable_dimensions": len(applicable),
        "status": status,
        "disclaimer": "评分只表示研究优先级，不是买入或卖出指令",
    }


def build_evidence(result: Mapping[str, Any]) -> list:
    evidence = []
    instrument = result["instrument"]
    for section_name in ("market", "technical", "valuation", "sections"):
        if section_name == "sections":
            sections = result.get("sections", {})
            for name, envelope in sections.items():
                evidence.extend(
                    _evidence_for_mapping(
                        instrument, name, envelope.get("data", {}), envelope
                    )
                )
            continue
        value = result.get(section_name)
        if isinstance(value, Mapping):
            evidence.extend(
                _evidence_for_mapping(
                    instrument, section_name, value.get("data", value), value
                )
            )
    unique = {}
    for item in evidence:
        unique[item["evidence_id"]] = item
    return list(unique.values())


def _evidence_for_mapping(
    instrument: Mapping[str, Any],
    section: str,
    data: Any,
    envelope: Mapping[str, Any],
) -> list:
    if isinstance(data, Mapping):
        values = data.items()
    else:
        values = [("value", data)]
    result = []
    for field, value in values:
        if isinstance(value, (list, dict)) and len(str(value)) > 4096:
            value = "[truncated]"
        provenance = envelope.get("provenance", {}) or {}
        basis = "{}:{}:{}:{}".format(
            instrument["market"], instrument["code"], section, field
        )
        evidence_id = "research:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
        result.append(
            {
                "evidence_id": evidence_id,
                "section": section,
                "field": field,
                "value": value,
                "data_as_of": envelope.get("data_as_of")
                or provenance.get("data_as_of"),
                "collected_at": provenance.get("collected_at"),
                "source_name": provenance.get("source_name"),
                "source_url": provenance.get("source_url"),
                "provider_id": provenance.get("provider_id"),
                "skill_name": provenance.get("skill_name"),
                "skill_version": provenance.get("skill_version"),
                "status": envelope.get("status", "unknown"),
            }
        )
    return result


def _section_envelope(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not value:
        return {
            "data": {},
            "provenance": {},
            "status": "missing",
            "error_reason": "Provider 未返回该分区",
        }
    envelope = dict(value)
    envelope.setdefault("data", {})
    envelope.setdefault("provenance", {})
    envelope.setdefault("status", "ok" if envelope["data"] else "missing")
    return envelope


def _build_provenance(provider, sections: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "provider_id": getattr(provider, "provider_id", "unknown"),
        "skill_name": getattr(provider, "skill_name", None),
        "skill_version": getattr(provider, "skill_version", None),
        "sections": {
            name: dict(value.get("provenance", {}) or {})
            for name, value in sections.items()
        },
    }


def _latest_as_of(sections: Mapping[str, Mapping[str, Any]]) -> Optional[str]:
    values = []
    for envelope in sections.values():
        value = envelope.get("data_as_of") or (envelope.get("provenance") or {}).get(
            "data_as_of"
        )
        if value:
            values.append(str(value))
    return max(values) if values else None


def _resolve_instrument_type(code: str, instrument_type: Optional[str]) -> str:
    if instrument_type is None:
        return "ETF" if code.startswith("5") else "STOCK"
    value = str(instrument_type).upper()
    if value not in {"STOCK", "ETF"}:
        raise ValueError("instrument_type 必须是 STOCK 或 ETF")
    return value


def _normalize_sections(sections: Optional[Iterable[str]]) -> tuple:
    if sections is None:
        return DEFAULT_SECTIONS
    values = tuple(dict.fromkeys(str(value).lower() for value in sections))
    unknown = sorted(set(values) - set(DEFAULT_SECTIONS))
    if unknown:
        raise ValueError("不支持的研究分区：{}".format(", ".join(unknown)))
    # Market and bars are the identity/technical base for every research card.
    return tuple(dict.fromkeys(("market", "bars") + values)) or DEFAULT_SECTIONS


def _market_valuation(market: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: market.get(key)
        for key in ("pe_ttm", "pb", "ps", "mcap_yi", "dividend_yield")
        if market.get(key) is not None
    }


def _valuation_score(data: Mapping[str, Any]) -> Dict[str, Any]:
    values = []
    reasons = []
    for key, scale in (("pe_ttm", 2.0), ("pb", 20.0)):
        value = _number(data.get(key))
        if value is not None and value > 0:
            values.append(max(0.0, min(100.0, 100.0 - value * scale)))
            reasons.append("{}={}".format(key, value))
    return _dimension(values, reasons, "缺少 PE/PB 等估值指标")


def _quality_score(instrument_type: str, data: Mapping[str, Any]) -> Dict[str, Any]:
    if instrument_type == "ETF":
        values = []
        reasons = []
        for key, scale in (("expense_ratio", 1000.0), ("tracking_error", 1000.0)):
            value = _number(data.get(key))
            if value is not None and value >= 0:
                values.append(max(0.0, min(100.0, 100.0 - value * scale)))
                reasons.append("{}={}".format(key, value))
        return _dimension(values, reasons, "缺少 ETF 费用或跟踪误差")
    roe = _number(data.get("roe"))
    debt = _number(data.get("debt_ratio"))
    values = []
    reasons = []
    if roe is not None:
        values.append(max(0.0, min(100.0, roe * 5.0)))
        reasons.append("roe={}".format(roe))
    if debt is not None:
        values.append(max(0.0, min(100.0, 100.0 - debt)))
        reasons.append("debt_ratio={}".format(debt))
    return _dimension(values, reasons, "缺少 ROE 或负债质量指标")


def _growth_score(
    instrument_type: str, fundamentals: Mapping[str, Any], capital: Mapping[str, Any]
) -> Dict[str, Any]:
    if instrument_type == "ETF":
        value = _number(fundamentals.get("benchmark_return_annual"))
        if value is None:
            return {"applicable": False, "score": None, "reasons": ["ETF 成长维度使用跟踪指数表现"]}
        return _dimension([max(0.0, min(100.0, 50.0 + value * 2.0))], ["benchmark_return_annual={}".format(value)], "")
    values = []
    reasons = []
    for key in ("revenue_growth", "profit_growth"):
        value = _number(fundamentals.get(key))
        if value is not None:
            values.append(max(0.0, min(100.0, 50.0 + value * 2.0)))
            reasons.append("{}={}".format(key, value))
    return _dimension(values, reasons, "缺少收入或利润增长率")


def _dividend_score(data: Mapping[str, Any]) -> Dict[str, Any]:
    value = _number(data.get("dividend_yield"))
    if value is None:
        return {"applicable": True, "score": None, "reasons": ["缺少股息率"]}
    return _dimension([max(0.0, min(100.0, value * 20.0))], ["dividend_yield={}".format(value)], "")


def _trend_score(indicators: Mapping[str, Any]) -> Dict[str, Any]:
    values = []
    reasons = []
    close = _number(indicators.get("latest_close"))
    ma20 = _number(indicators.get("ma20"))
    volatility = _number(indicators.get("annualized_volatility20"))
    drawdown = _number(indicators.get("max_drawdown"))
    if close is not None and ma20 not in (None, 0):
        values.append(max(0.0, min(100.0, 50.0 + (close / ma20 - 1.0) * 500.0)))
        reasons.append("close_vs_ma20={}".format(close / ma20 - 1.0))
    if volatility is not None:
        values.append(max(0.0, min(100.0, 100.0 - volatility * 100.0)))
        reasons.append("volatility20={}".format(volatility))
    if drawdown is not None:
        values.append(max(0.0, min(100.0, 100.0 + drawdown * 100.0)))
        reasons.append("max_drawdown={}".format(drawdown))
    return _dimension(values, reasons, "缺少足够日线计算趋势与风险")


def _dimension(values, reasons, missing_reason):
    if not values:
        return {
            "applicable": True,
            "score": None,
            "raw_values": [],
            "reasons": [missing_reason],
        }
    return {
        "applicable": True,
        "score": round(sum(values) / len(values), 4),
        "raw_values": list(values),
        "reasons": reasons,
    }


def _merge_dict(left: Mapping[str, Any], right: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(right)
    merged.update(dict(left))
    return merged


def _rolling_mean(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _rsi(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) <= period:
        return None
    gains = []
    losses = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        delta = current - previous
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0 if average_gain else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def _ema(values: Sequence[float], period: int) -> list:
    if not values:
        return []
    multiplier = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append((float(value) - result[-1]) * multiplier + result[-1])
    return result


def _macd(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if len(values) < 26:
        return {"line": None, "signal": None, "histogram": None}
    fast = _ema(values, 12)
    slow = _ema(values, 26)
    line = [left - right for left, right in zip(fast, slow)]
    signal = _ema(line, 9)
    return {
        "line": line[-1],
        "signal": signal[-1],
        "histogram": line[-1] - signal[-1],
    }


def _bollinger(values: Sequence[float], period: int) -> Dict[str, Optional[float]]:
    if len(values) < period:
        return {"middle": None, "upper": None, "lower": None}
    window = values[-period:]
    middle = sum(window) / period
    deviation = math.sqrt(sum((value - middle) ** 2 for value in window) / period)
    return {"middle": middle, "upper": middle + 2 * deviation, "lower": middle - 2 * deviation}


def _atr(bars: Sequence[Mapping[str, Any]], period: int) -> Optional[float]:
    if len(bars) <= period:
        return None
    true_ranges = []
    for previous, current in zip(bars[-period - 1 : -1], bars[-period:]):
        high = _number(current.get("high"))
        low = _number(current.get("low"))
        previous_close = _number(previous.get("close"))
        if high is None or low is None or previous_close is None:
            continue
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(true_ranges) / len(true_ranges) if true_ranges else None


def _annualized_volatility(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def _max_drawdown(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    peak = values[0]
    result = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            result = min(result, value / peak - 1.0)
    return result


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "", "--", "-") else None
    except (TypeError, ValueError):
        return None
