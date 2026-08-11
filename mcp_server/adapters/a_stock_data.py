"""Small adapter around the public Tencent endpoint documented by a-stock-data.

The skill remains the source-of-truth for routing and fallback policy. This module
keeps the MCP boundary independent from the skill's embedded helper code.
"""

from datetime import datetime
import re
from typing import Any, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from mcp_server.domain.models import MarketSnapshot, WatchlistItem


SOURCE_URL = "https://qt.gtimg.cn/"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_tencent_quote_response(
    raw: str, key_of: Mapping[str, str]
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for line in raw.split(";"):
        if "=" not in line or '"' not in line:
            continue
        key = line.split("=", 1)[0].split("_")[-1].lower()
        if key not in key_of:
            continue
        quoted = line.split('"', 2)[1]
        values = quoted.split("~")
        if len(values) < 47:
            continue
        code = key_of[key]
        result[code] = {
            "name": values[1],
            "price": _float(values, 3),
            "last_close": _float(values, 4),
            "change_pct": _float(values, 32),
            "amount_wan": _float(values, 37),
            "turnover_pct": _float(values, 38),
            "pe_ttm": _positive_or_none(values, 39),
            "mcap_yi": _positive_or_none(values, 45),
            "pb": _positive_or_none(values, 46),
            "quote_time": values[30] if len(values) > 30 else None,
        }
        price = result[code]["price"]
        last_close = result[code]["last_close"]
        amount = result[code]["amount_wan"]
        result[code]["is_stale"] = bool(
            amount == 0 and price and last_close and price == last_close
        )
    return result


class TencentMarketDataProvider:
    """Fetch real-time/paused-session quotes for the watchlist."""

    def __init__(
        self, session=None, timeout: int = 10, skill=None, bypass_proxy: bool = True
    ):
        if session is None:
            try:
                import requests

                session = requests.Session()
                session.headers.update({"User-Agent": "Mozilla/5.0"})
            except ImportError as exc:
                raise RuntimeError("请先安装 requests，或注入兼容的 HTTP session") from exc
        self.session = session
        self.session.trust_env = not bypass_proxy
        self.timeout = timeout
        self.skill = skill

    def snapshots_for(
        self,
        items: Iterable[WatchlistItem],
        cutoff: str,
        report_date=None,
    ) -> list:
        watchlist = list(items)
        if not watchlist:
            return []
        try:
            quotes = self._quotes_for(watchlist)
        except Exception as exc:
            return [_missing_snapshot(item, str(exc), self.skill) for item in watchlist]

        snapshots = []
        for item in watchlist:
            quote = quotes.get(item.code)
            if quote is None:
                snapshots.append(
                    _missing_snapshot(item, "行情接口没有返回该代码", self.skill)
                )
                continue
            warnings = []
            if quote.get("is_stale"):
                warnings.append("成交额为 0，报价可能是停牌或非当日成交数据")
            as_of = _parse_quote_time(quote.get("quote_time"))
            if as_of is None:
                warnings.append("行情接口未返回明确报价时间")
                if report_date is not None:
                    snapshots.append(
                        _missing_snapshot(
                            item,
                            "行情接口未返回明确报价时间，已拒绝混入午间报告",
                            self.skill,
                        )
                    )
                    continue
            if report_date is not None and as_of is not None and as_of.date() != report_date:
                snapshots.append(
                    _missing_snapshot(
                        item,
                        "报价日期 {} 与报告日期 {} 不一致，已拒绝混入午间报告".format(
                            as_of.date().isoformat(), report_date.isoformat()
                        ),
                        self.skill,
                    )
                )
                continue
            cutoff_time = _parse_cutoff_time(cutoff)
            if as_of is not None and cutoff_time is not None and as_of.time() > cutoff_time:
                snapshots.append(
                    _missing_snapshot(
                        item,
                        "报价时间 {} 晚于报告截止 {}，已拒绝混入午间报告".format(
                            as_of.isoformat(), cutoff
                        ),
                        self.skill,
                    )
                )
                continue
            signals = _signals(quote.get("change_pct"))
            snapshots.append(
                MarketSnapshot(
                    code=item.code,
                    name=quote.get("name") or item.name or "未命名标的",
                    instrument_type=item.instrument_type,
                    price=quote.get("price"),
                    last_close=quote.get("last_close"),
                    change_pct=quote.get("change_pct"),
                    amount_wan=quote.get("amount_wan"),
                    turnover_pct=quote.get("turnover_pct"),
                    pe_ttm=quote.get("pe_ttm"),
                    pb=quote.get("pb"),
                    as_of=as_of,
                    source_name="腾讯财经（a-stock-data 行情层）",
                    source_url=SOURCE_URL,
                    status="partial" if warnings else "ok",
                    signals=signals,
                    warnings=warnings,
                    skill_name=getattr(self.skill, "name", "a-stock-data"),
                    skill_version=getattr(self.skill, "version", None),
                )
            )
        return snapshots

    def _quotes_for(self, items: Iterable[WatchlistItem]) -> Dict[str, Dict[str, Any]]:
        prefixes = []
        key_of = {}
        for item in items:
            key = item.market.lower() + item.code
            prefixes.append(key)
            key_of[key] = item.code
        url = "https://qt.gtimg.cn/q=" + ",".join(prefixes)
        response = self.session.get(url, timeout=self.timeout)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        raw = _response_text(response)
        parsed = parse_tencent_quote_response(raw, key_of)
        if not parsed:
            raise RuntimeError("腾讯行情接口返回空结果")
        return parsed


def _response_text(response) -> str:
    content = getattr(response, "content", None)
    if content is not None and isinstance(content, bytes):
        return content.decode("gbk", errors="replace")
    return str(getattr(response, "text", ""))


def _float(values, index: int) -> Optional[float]:
    try:
        value = values[index].strip()
        return float(value) if value not in {"", "--", "-"} else None
    except (IndexError, TypeError, ValueError):
        return None


def _positive_or_none(values, index: int) -> Optional[float]:
    value = _float(values, index)
    return value if value is not None and value > 0 else None


def _parse_quote_time(value: Optional[str]):
    if not value:
        return None
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=SHANGHAI)
        except ValueError:
            continue
    return None


def _parse_cutoff_time(value: str):
    match = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return None
    from datetime import time

    return time(int(match.group(1)), int(match.group(2)))


def _signals(change_pct: Optional[float]):
    if change_pct is None:
        return []
    if change_pct >= 5:
        return ["上午涨幅较大（≥5%）"]
    if change_pct <= -5:
        return ["上午跌幅较大（≤-5%）"]
    return []


def _missing_snapshot(item: WatchlistItem, error: str, skill=None) -> MarketSnapshot:
    return MarketSnapshot(
        code=item.code,
        name=item.name or "未命名标的",
        instrument_type=item.instrument_type,
        price=None,
        last_close=None,
        change_pct=None,
        amount_wan=None,
        turnover_pct=None,
        pe_ttm=None,
        pb=None,
        as_of=None,
        source_name="腾讯财经（a-stock-data 行情层）",
        source_url=SOURCE_URL,
        status="partial",
        warnings=["行情数据缺失"],
        errors=[error],
        skill_name=getattr(skill, "name", "a-stock-data"),
        skill_version=getattr(skill, "version", None),
    )
