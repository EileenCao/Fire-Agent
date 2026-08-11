"""No-login adapter for explicitly configured public RSS/Atom or HTML feeds."""

import re
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional
from xml.etree import ElementTree

from mcp_server.domain.identifiers import normalize_ticker
from mcp_server.services.sentiment import normalize_document


class PublicWebSentimentProvider:
    provider_id = "public-web"
    skill_name = None
    skill_version = None

    def __init__(self, session=None):
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    def collect_documents(
        self,
        source: Mapping[str, Any],
        code: Optional[str] = None,
        market: Optional[str] = None,
        instrument_type: str = "STOCK",
        as_of: Optional[date] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        del refresh
        config = dict(source.get("config") or {})
        urls = config.get("urls") or ([config.get("url")] if config.get("url") else [])
        urls = [str(url).strip() for url in urls if str(url).strip()]
        if not urls:
            return {"documents": [], "warnings": ["public-web provider requires an explicit public URL"]}
        normalized_code = None
        if code:
            normalized_code, _ = normalize_ticker(code, market)
        documents: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for url in urls:
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                rows = _parse_feed(response.text)
                if not rows:
                    warnings.append("public-web URL did not expose RSS/Atom entries: {}".format(url))
                    continue
                for row in rows:
                    published_at = row.get("published_at")
                    if not published_at:
                        warnings.append("public-web entry missing published_at: {}".format(url))
                        continue
                    content = "\n".join(
                        value for value in (row.get("title"), row.get("summary")) if value
                    )
                    if not content:
                        continue
                    documents.append(
                        normalize_document(
                            {
                                "platform": source.get("platform") or "public-web",
                                "source_id": source.get("source_id") or url,
                                "document_type": source.get("source_type") or "news",
                                "author_id": source.get("author_id"),
                                "author_name": source.get("display_name"),
                                "canonical_url": row.get("url") or url,
                                "published_at": published_at,
                                "summary": content,
                                "content": content,
                                "targets": [normalized_code] if normalized_code else [],
                                "shenwan_industries": config.get("shenwan_industries") or [],
                                "concept_tags": config.get("concept_tags") or [],
                                "metadata": {
                                    "provider_id": self.provider_id,
                                    "source_url": url,
                                    "data_as_of": published_at,
                                    "methodology": "explicit public URL without credentials or login",
                                },
                            }
                        )
                    )
            except Exception as exc:
                warnings.append("public-web collection failed for {}: {}".format(url, exc))
        return {"documents": documents, "warnings": warnings}


def _parse_feed(text: str) -> List[Dict[str, str]]:
    try:
        root = ElementTree.fromstring(text)
        items = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
        rows = []
        for item in items:
            link = _first_text(item, {"link"})
            if not link:
                for child in item:
                    if _local_name(child.tag) == "link" and child.attrib.get("href"):
                        link = child.attrib["href"]
                        break
            rows.append(
                {
                    "title": _clean(_first_text(item, {"title"})),
                    "summary": _clean(_first_text(item, {"description", "summary", "content"})),
                    "published_at": _parse_published(_first_text(item, {"pubDate", "published", "updated"})),
                    "url": link,
                }
            )
        return rows
    except ElementTree.ParseError:
        return []


def _first_text(element, names):
    for child in element.iter():
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _local_name(tag):
    return str(tag).rsplit("}", 1)[-1]


def _clean(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def _parse_published(value):
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M %z"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return text
