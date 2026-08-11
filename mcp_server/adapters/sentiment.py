"""Explicit sentiment-source adapters.

The first adapter reuses the already configured a-stock-data instrument
provider for public financial news.  Restricted platforms remain manual and
are never accessed through login or anti-bot workarounds.
"""

from datetime import date
from typing import Any, Dict, List, Mapping, Optional

from mcp_server.domain.identifiers import normalize_ticker
from mcp_server.services.sentiment import normalize_document


class AStockDataSentimentProvider:
    provider_id = "a-stock-data"

    def __init__(self, instrument_provider):
        self.instrument_provider = instrument_provider
        self.skill_name = getattr(instrument_provider, "skill_name", "a-stock-data")
        self.skill_version = getattr(instrument_provider, "skill_version", "unknown")

    def collect_documents(
        self,
        source: Mapping[str, Any],
        code: Optional[str] = None,
        market: Optional[str] = None,
        instrument_type: str = "STOCK",
        as_of: Optional[date] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        source_type = str(source.get("source_type") or "news").lower()
        if source_type != "news":
            return {
                "documents": [],
                "warnings": [
                    "{} 来源需要 manual 用户投递链接、文本或截图，当前采集器不会登录或绕过反爬".format(
                        source.get("platform") or "该"
                    )
                ],
            }
        if not code:
            return {"documents": [], "warnings": ["a-stock-data 新闻采集需要明确标的代码"]}
        normalized_code, normalized_market = normalize_ticker(code, market)
        instrument = {
            "code": normalized_code,
            "market": normalized_market,
            "instrument_type": str(instrument_type or "STOCK").upper(),
        }
        collected = self.instrument_provider.collect(
            instrument, ["news"], as_of=as_of, refresh=refresh
        )
        envelope = collected.get("news") or {}
        rows = envelope.get("data") or []
        if not rows:
            return {
                "documents": [],
                "warnings": [
                    str(envelope.get("error_reason") or "a-stock-data 新闻接口没有返回内容")
                ],
            }
        config = dict(source.get("config") or {})
        documents: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                warnings.append("第 {} 条新闻不是对象，已跳过".format(index + 1))
                continue
            title = str(row.get("title") or "").strip()
            content = str(row.get("content") or "").strip()
            summary = "：".join(part for part in (title, content) if part)
            published_at = row.get("time") or row.get("date") or envelope.get("data_as_of")
            if not summary or not published_at:
                warnings.append("第 {} 条新闻缺少摘要或发布时间，已跳过".format(index + 1))
                continue
            documents.append(
                normalize_document(
                    {
                        "platform": source.get("platform") or "financial-news",
                        "source_id": source.get("source_id") or "a-stock-data-news",
                        "document_type": "news",
                        "author_id": source.get("author_id"),
                        "author_name": source.get("display_name"),
                        "canonical_url": row.get("url") or envelope.get("provenance", {}).get("source_url"),
                        "published_at": published_at,
                        "summary": summary,
                        "content": "{}\n{}".format(title, content),
                        "targets": [normalized_code] + list(config.get("targets") or []),
                        "shenwan_industries": config.get("shenwan_industries") or [],
                        "concept_tags": config.get("concept_tags") or [],
                        "metadata": {
                            "source_name": row.get("source") or envelope.get("provenance", {}).get("source_name"),
                            "source_url": envelope.get("provenance", {}).get("source_url"),
                            "data_as_of": envelope.get("data_as_of") or row.get("time") or row.get("date"),
                            "provider_id": self.provider_id,
                            "skill_name": self.skill_name,
                            "skill_version": self.skill_version,
                            "methodology": "a-stock-data normalized public news adapter",
                        },
                    }
                )
            )
        return {"documents": documents, "warnings": warnings}
