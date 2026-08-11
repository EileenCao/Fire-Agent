"""a-stock-data backed provider for the instrument research card.

The provider deliberately keeps source routing at the adapter boundary. The
research service only consumes normalized section envelopes and never knows
which upstream endpoint produced a field.
"""

import json
import re
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from mcp_server.domain.models import WatchlistItem


RESEARCH_TZ = ZoneInfo("Asia/Shanghai")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class AStockDataInstrumentProvider:
    provider_id = "a-stock-data"

    def __init__(
        self,
        market_provider,
        historical_data_provider,
        skill=None,
        session=None,
        section_fetchers: Optional[Mapping[str, Callable[..., Any]]] = None,
        eastmoney_min_interval: float = 1.0,
        bypass_proxy: bool = True,
    ):
        self.market_provider = market_provider
        self.historical_data_provider = historical_data_provider
        self.skill = skill or getattr(market_provider, "skill", None)
        self.skill_name = getattr(self.skill, "name", "a-stock-data")
        self.skill_version = getattr(self.skill, "version", "unknown")
        if session is None:
            import requests

            session = requests.Session()
            session.headers.update({"User-Agent": UA})
        self.session = session
        self.session.trust_env = not bypass_proxy
        self.section_fetchers = dict(section_fetchers or {})
        self.eastmoney_min_interval = max(0.0, float(eastmoney_min_interval))
        self._last_eastmoney_request = 0.0

    def collect(
        self,
        instrument: Mapping[str, Any],
        sections: Iterable[str],
        as_of: Optional[date] = None,
        refresh: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        del refresh  # cache policy belongs to the composed market/history providers
        result: Dict[str, Dict[str, Any]] = {}
        for section in sections:
            name = str(section).lower()
            try:
                if name == "market":
                    result[name] = self._market(instrument, as_of)
                elif name == "bars":
                    result[name] = self._bars(instrument, as_of)
                elif name == "valuation":
                    result[name] = self._valuation(instrument, result.get("market"))
                else:
                    result[name] = self._optional(name, instrument, as_of)
            except Exception as exc:
                result[name] = self._error(name, str(exc))
        return result

    def _market(self, instrument, as_of):
        item = WatchlistItem(
            code=instrument["code"],
            market=instrument["market"],
            instrument_type=instrument["instrument_type"],
            name=instrument.get("name"),
        )
        snapshots = self.market_provider.snapshots_for([item], cutoff="23:59")
        if not snapshots:
            return self._missing("market", "行情 Provider 未返回标的")
        snapshot = snapshots[0]
        data = asdict(snapshot)
        as_of_value = data.get("as_of")
        if isinstance(as_of_value, datetime):
            as_of_value = as_of_value.isoformat()
        data["as_of"] = as_of_value
        provenance = self._provenance(
            snapshot.source_name,
            snapshot.source_url,
            as_of_value,
            snapshot.skill_version,
        )
        status = snapshot.status or ("partial" if snapshot.warnings else "ok")
        return {
            "data": data,
            "provenance": provenance,
            "data_as_of": as_of_value,
            "status": status,
            "error_reason": "; ".join(snapshot.errors) if snapshot.errors else None,
        }

    def _bars(self, instrument, as_of):
        end = as_of or datetime.now(RESEARCH_TZ).date()
        start = end - timedelta(days=365)
        fetched = self.historical_data_provider.fetch(
            [instrument["code"]], start, end
        )
        bars = fetched.data.get(instrument["code"], [])
        if not bars:
            return self._missing(
                "bars",
                fetched.errors.get(instrument["code"], "历史 Provider 未返回日线"),
                fetched.provenance,
            )
        return {
            "data": bars,
            "provenance": dict(fetched.provenance),
            "data_as_of": fetched.provenance.get("data_end"),
            "status": "partial" if fetched.errors else "ok",
            "error_reason": "; ".join(fetched.errors.values()) if fetched.errors else None,
        }

    def _valuation(self, instrument, market_envelope):
        market = (market_envelope or {}).get("data", {})
        data = {
            key: market.get(key)
            for key in ("pe_ttm", "pb", "ps", "mcap_yi", "dividend_yield")
            if market.get(key) is not None
        }
        if instrument["instrument_type"] == "ETF":
            data.update(
                {
                    "nav": market.get("nav"),
                    "premium_discount": market.get("premium_discount"),
                }
            )
        provenance = dict((market_envelope or {}).get("provenance", {}) or {})
        if not data:
            return self._missing("valuation", "行情来源没有返回可用估值字段", provenance)
        return {
            "data": data,
            "provenance": provenance,
            "data_as_of": (market_envelope or {}).get("data_as_of"),
            "status": "ok",
        }

    def _optional(self, name, instrument, as_of):
        fetcher = self.section_fetchers.get(name)
        if fetcher is not None:
            value = fetcher(instrument, as_of=as_of)
            if isinstance(value, Mapping) and "data" in value:
                return dict(value)
            return {
                "data": value,
                "provenance": self._provenance(
                    "custom section fetcher", "", None, self.skill_version
                ),
                "status": "ok" if value else "missing",
            }
        if name == "fundamentals":
            if instrument["instrument_type"] == "ETF":
                return self._etf_metadata()
            return self._fundamentals(instrument)
        if name == "capital":
            return self._capital(instrument)
        if name == "news":
            return self._news(instrument)
        if name == "announcements":
            return self._announcements(instrument)
        return self._missing(name, "首期 Provider 尚未提供该研究分区")

    def _etf_metadata(self):
        # Keep planned ETF fields visible as explicit missing evidence until
        # a-stock-data exposes a normalized ETF-fund metadata endpoint.
        fields = {
            "tracking_index": None,
            "nav": None,
            "premium_discount": None,
            "fund_size": None,
            "component_concentration": None,
            "sector_exposure": None,
            "tracking_error": None,
            "expense_ratio": None,
        }
        return {
            "data": fields,
            "provenance": self._provenance(
                "a-stock-data ETF metadata", None, None, self.skill_version
            ),
            "status": "missing",
            "error_reason": "a-stock-data 首期适配层未提供 ETF 基金资料字段",
        }

    def _fundamentals(self, instrument):
        if instrument["instrument_type"] == "ETF":
            return self._missing("fundamentals", "ETF 不适用公司财务三表")
        code = instrument["code"]
        parts = []
        errors = []
        info = {}
        try:
            info = self._eastmoney_stock_info(code)
        except Exception as exc:
            errors.append("个股基本面信息：{}".format(exc))
        reports = {}
        for report_type in ("lrb", "fzb", "llb"):
            try:
                reports[report_type] = self._sina_financial_report(code, report_type)
            except Exception as exc:
                errors.append("{} 财报：{}".format(report_type, exc))
        data = {"profile": info, "reports": reports}
        data.update(_derive_fundamental_metrics(reports))
        status = "ok" if info or reports else "missing"
        if errors and status == "ok":
            status = "partial"
        return {
            "data": data,
            "provenance": self._provenance(
                "东财/新浪（a-stock-data 基本面层）",
                "https://push2.eastmoney.com/api/qt/stock/get",
                None,
                self.skill_version,
            ),
            "status": status,
            "error_reason": "; ".join(errors) if errors else None,
        }

    def _capital(self, instrument):
        code = instrument["code"]
        if instrument["instrument_type"] == "ETF":
            return self._missing("capital", "首期资金面接口只对股票开放")
        market_code = 1 if instrument["market"] == "SH" else 0
        url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "secid": "{}.{}".format(market_code, code),
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "lmt": "120",
        }
        response = self._eastmoney_get(url, params=params)
        rows = []
        for line in (response.json().get("data") or {}).get("klines", []) or []:
            parts = str(line).split(",")
            if len(parts) < 6:
                continue
            rows.append(
                {
                    "date": parts[0],
                    "main_net": _float_or_none(parts[1]),
                    "small_net": _float_or_none(parts[2]),
                    "mid_net": _float_or_none(parts[3]),
                    "large_net": _float_or_none(parts[4]),
                    "super_net": _float_or_none(parts[5]),
                }
            )
        if not rows:
            return self._missing("capital", "资金流接口未返回数据")
        return {
            "data": {"fund_flow_120d": rows},
            "provenance": self._provenance(
                "东财（a-stock-data 资金流层）", url, rows[-1]["date"], self.skill_version
            ),
            "data_as_of": rows[-1]["date"],
            "status": "ok",
        }

    def _news(self, instrument):
        code = instrument["code"]
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        inner = json.dumps(
            {
                "uid": "",
                "keyword": code,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default",
                        "sort": "default",
                        "pageIndex": 1,
                        "pageSize": 20,
                        "preTag": "",
                        "postTag": "",
                    }
                },
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        response = self._eastmoney_get(
            url,
            params={"cb": "jQuery_news", "param": inner},
            referer="https://so.eastmoney.com/",
        )
        text = response.text
        payload = json.loads(text[text.index("(") + 1 : text.rindex(")")])
        articles = (payload.get("result") or {}).get("cmsArticleWebOld") or []
        rows = []
        for item in articles:
            rows.append(
                {
                    "title": _strip_html(item.get("title", "")),
                    "content": _strip_html(item.get("content", ""))[:200],
                    "time": item.get("date", ""),
                    "source": item.get("mediaName", ""),
                    "url": item.get("url", ""),
                }
            )
        if not rows:
            return self._missing("news", "东财新闻接口未返回文章", {"source_url": url})
        return {
            "data": rows,
            "provenance": self._provenance(
                "东财（a-stock-data 新闻层）", url, rows[0].get("time"), self.skill_version
            ),
            "data_as_of": rows[0].get("time"),
            "status": "ok",
        }

    def _announcements(self, instrument):
        code = instrument["code"]
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        if instrument["market"] == "SH":
            org_id = "gssh0{}".format(code)
        elif instrument["market"] == "BJ":
            org_id = "gsbj0{}".format(code)
        else:
            org_id = "gssz0{}".format(code)
        payload = {
            "stock": "{},{}".format(code, org_id),
            "tabName": "fulltext",
            "pageSize": "30",
            "pageNum": "1",
            "column": "",
            "category": "",
            "plate": "",
            "seDate": "",
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        response = self.session.post(
            url,
            data=payload,
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.cninfo.com.cn/new/disclosure",
                "Origin": "https://www.cninfo.com.cn",
            },
            timeout=15,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        rows = []
        for item in response.json().get("announcements", []) or []:
            anno_id = item.get("announcementId", "")
            rows.append(
                {
                    "title": item.get("announcementTitle", ""),
                    "type": item.get("announcementTypeName", ""),
                    "date": str(item.get("announcementTime", ""))[:10],
                    "url": "https://www.cninfo.com.cn/new/disclosure/detail?annoId={}".format(anno_id),
                }
            )
        if not rows:
            return self._missing("announcements", "巨潮公告接口未返回公告", {"source_url": url})
        return {
            "data": rows,
            "provenance": self._provenance("巨潮（a-stock-data 公告层）", url, rows[0]["date"], self.skill_version),
            "data_as_of": rows[0]["date"],
            "status": "ok",
        }

    def _eastmoney_stock_info(self, code):
        market_code = 1 if code.startswith(("5", "6", "9")) else 0
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        response = self._eastmoney_get(
            url,
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
                "secid": "{}.{}".format(market_code, code),
            },
        )
        data = response.json().get("data") or {}
        return {
            "code": data.get("f57", code),
            "name": data.get("f58", ""),
            "industry": data.get("f127", ""),
            "total_shares": data.get("f84"),
            "float_shares": data.get("f85"),
            "mcap": data.get("f116"),
            "float_mcap": data.get("f117"),
            "list_date": str(data.get("f189", "")),
            "price": data.get("f43"),
        }

    def _sina_financial_report(self, code, report_type):
        prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
        url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
        response = self.session.get(
            url,
            params={
                "paperCode": prefix + code,
                "source": report_type,
                "type": "0",
                "page": "1",
                "num": "8",
            },
            timeout=15,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        report_list = (
            response.json().get("result", {}).get("data", {}).get("report_list", {}) or {}
        )
        rows = []
        for period in sorted(report_list, reverse=True)[:8]:
            obj = report_list[period]
            row = {"report_date": "{}-{}-{}".format(period[:4], period[4:6], period[6:8])}
            for item in obj.get("data", []) or []:
                title = item.get("item_title", "")
                if title and item.get("item_value") is not None:
                    row[title] = item.get("item_value")
                    if item.get("item_tongbi") not in (None, ""):
                        row[title + "_同比"] = item.get("item_tongbi")
            rows.append(row)
        return rows

    def _eastmoney_get(self, url, params=None, referer="https://quote.eastmoney.com/"):
        elapsed = time.monotonic() - self._last_eastmoney_request
        if elapsed < self.eastmoney_min_interval:
            time.sleep(self.eastmoney_min_interval - elapsed)
        response = self.session.get(
            url,
            params=params or {},
            headers={"User-Agent": UA, "Referer": referer},
            timeout=20,
        )
        self._last_eastmoney_request = time.monotonic()
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response

    def _provenance(self, source_name, source_url, data_as_of, skill_version=None):
        return {
            "source_name": source_name,
            "source_url": source_url,
            "provider_id": self.provider_id,
            "skill_name": self.skill_name,
            "skill_version": skill_version or self.skill_version,
            "data_as_of": data_as_of,
            "collected_at": datetime.now(RESEARCH_TZ).isoformat(),
            "methodology": "a-stock-data source routing and normalized adapter",
        }

    def _missing(self, section, reason, provenance=None):
        return {
            "data": {},
            "provenance": dict(provenance or {}),
            "status": "missing",
            "error_reason": reason,
        }

    def _error(self, section, reason):
        return {
            "data": {},
            "provenance": self._provenance(
                "a-stock-data adapter", None, None, self.skill_version
            ),
            "status": "error",
            "error_reason": reason,
        }


def _derive_fundamental_metrics(reports):
    latest = next(iter(reports.get("lrb", []) or []), {})
    values = {}
    for key, value in latest.items():
        if not key.endswith("同比"):
            continue
        numeric = _float_or_none(value)
        if numeric is None:
            continue
        if "营业收入" in key:
            values["revenue_growth"] = numeric
        elif "净利润" in key or "归母" in key:
            values["profit_growth"] = numeric
    return values


def _float_or_none(value):
    try:
        return float(value) if value not in (None, "", "--", "-") else None
    except (TypeError, ValueError):
        return None


def _strip_html(value):
    return re.sub(r"<[^>]+>", "", str(value or ""))
