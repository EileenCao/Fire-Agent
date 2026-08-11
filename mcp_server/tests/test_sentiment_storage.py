import pytest

from mcp_server.services.sentiment import normalize_document, normalize_extraction
from mcp_server.storage import SQLiteStore


def _document(document_id="doc-1"):
    return normalize_document(
        {
            "document_id": document_id,
            "platform": "xueqiu",
            "source_id": "blogger-1",
            "author_id": "author-1",
            "canonical_url": "https://example.test/{}".format(document_id),
            "published_at": "2026-08-10T10:00:00+08:00",
            "collected_at": "2026-08-10T10:01:00+08:00",
            "content": "source text",
            "summary": "source summary",
            "document_type": "blogger",
            "targets": ["512890"],
            "shenwan_industries": ["银行"],
        }
    )


def test_sentiment_document_and_extraction_are_versioned_without_raw_content(tmp_path):
    store = SQLiteStore(tmp_path / "sentiment.sqlite3")
    store.initialize()
    store.upsert_sentiment_source(
        {
            "source_id": "blogger-1",
            "platform": "xueqiu",
            "source_type": "blogger",
            "author_id": "author-1",
            "display_name": "Fixture Author",
        }
    )

    document = store.ingest_sentiment_document(_document())
    loaded = store.get_sentiment_document("doc-1")
    assert loaded["content_hash"] == document["content_hash"]
    assert "content" not in loaded

    extraction = normalize_extraction(
        "doc-1",
        {"summary": "structured", "claims": [{"direction": 1}]},
        "fixture-model",
        "prompt-v1",
    )
    context = store.get_sentiment_extraction_context(["doc-1"])
    saved = store.save_sentiment_extraction(
        "doc-1", context["context_hash"], extraction
    )
    assert saved["version"] == 1
    assert store.list_sentiment_extractions("doc-1")[0]["extraction"] == extraction


def test_sentiment_extraction_rejects_stale_context_and_snapshot_is_immutable(tmp_path):
    store = SQLiteStore(tmp_path / "sentiment.sqlite3")
    store.initialize()
    store.ingest_sentiment_document(_document())
    extraction = normalize_extraction(
        "doc-1", {"claims": [{"direction": -1}]}, "fixture", "v1"
    )
    try:
        store.save_sentiment_extraction("doc-1", "stale", extraction)
    except ValueError as exc:
        assert "context" in str(exc)
    else:
        raise AssertionError("stale sentiment context should be rejected")

    snapshot = {
        "profile": "sentiment-baseline-v1",
        "scope": {"type": "instrument", "key": "512890"},
        "snapshot_date": "2026-08-10",
        "cutoff": "15:00",
        "factors": {"news_event_sentiment": {"value": 10}},
        "evidence": [{"evidence_id": "sentiment:doc-1", "document_id": "doc-1"}],
    }
    record = store.save_sentiment_snapshot(snapshot)
    loaded = store.get_sentiment_snapshot(record["id"])
    assert loaded["snapshot"] == snapshot
    assert store.list_sentiment_evidence(record["id"])[0]["evidence_id"] == "sentiment:doc-1"


def test_sentiment_duplicate_url_and_unknown_evidence_are_rejected(tmp_path):
    store = SQLiteStore(tmp_path / "sentiment.sqlite3")
    store.initialize()
    first = store.ingest_sentiment_document(
        {
            "platform": "xueqiu",
            "source_id": "author-1",
            "document_type": "blogger",
            "canonical_url": "https://example.test/post/1",
            "published_at": "2026-08-10T10:00:00+08:00",
            "content": "first version",
            "summary": "first version",
        }
    )
    duplicate = store.ingest_sentiment_document(
        {
            "platform": "xueqiu",
            "source_id": "author-1",
            "document_type": "blogger",
            "canonical_url": "https://example.test/post/1",
            "published_at": "2026-08-10T10:00:00+08:00",
            "content": "edited repost",
            "summary": "edited repost",
        }
    )

    assert duplicate["document_id"] == first["document_id"]

    context = store.get_sentiment_extraction_context([first["document_id"]])
    with pytest.raises(ValueError, match="evidence"):
        store.save_sentiment_extraction(
            first["document_id"],
            context["context_hash"],
            {
                "document_id": first["document_id"],
                "claims": [],
                "evidence_refs": ["unknown-evidence"],
                "extraction_model": "fixture",
                "prompt_version": "v1",
            },
        )


def test_sentiment_event_fingerprint_deduplicates_reposts(tmp_path):
    store = SQLiteStore(tmp_path / "sentiment.sqlite3")
    store.initialize()
    first = store.ingest_sentiment_document(
        {
            "platform": "eastmoney",
            "source_id": "news-1",
            "document_type": "news",
            "canonical_url": "https://example.test/news/1",
            "published_at": "2026-08-10T10:00:00+08:00",
            "content": "different raw text one",
            "summary": "same event summary",
            "targets": ["512890"],
        }
    )
    repost = store.ingest_sentiment_document(
        {
            "platform": "eastmoney",
            "source_id": "news-2",
            "document_type": "news",
            "canonical_url": "https://example.test/news/repost",
            "published_at": "2026-08-10T12:00:00+08:00",
            "content": "different raw text two",
            "summary": "same event summary",
            "targets": ["512890"],
        }
    )

    assert repost["document_id"] == first["document_id"]
