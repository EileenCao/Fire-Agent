from datetime import date, timedelta

import pytest

from mcp_server.services.sentiment import (
    aggregate_sentiment,
    build_sentiment_snapshot,
    build_extraction_context,
    evaluate_author_performance,
    normalize_document,
    normalize_extraction,
    rolling_percentile,
)


def _document(
    document_id="doc-1",
    document_type="news",
    published_at="2026-08-10T10:00:00+08:00",
    content="market update",
    author_id=None,
    targets=None,
):
    return normalize_document(
        {
            "document_id": document_id,
            "platform": "fixture",
            "source_id": "fixture-news",
            "author_id": author_id,
            "canonical_url": "https://example.test/{}".format(document_id),
            "published_at": published_at,
            "collected_at": "2026-08-10T10:01:00+08:00",
            "content": content,
            "summary": content,
            "document_type": document_type,
            "targets": targets or ["512890"],
        }
    )


def _extraction(document_id, direction, horizon=5, confidence=1.0, relevance=1.0):
    return normalize_extraction(
        document_id,
        {
            "summary": "structured summary",
            "claims": [
                {
                    "direction": direction,
                    "confidence": confidence,
                    "relevance": relevance,
                    "time_horizon": horizon,
                    "event_type": "opinion",
                    "targets": ["512890"],
                }
            ],
        },
        model="fixture-model",
        prompt_version="sentiment-test-v1",
    )


def test_normalize_document_persists_hash_but_not_full_content():
    document = _document(content="secret source text")

    assert document["content_hash"]
    assert document["summary"] == "secret source text"
    assert "content" not in document
    assert document["document_id"] == "doc-1"


def test_extraction_context_is_stable_and_cutoff_excludes_late_documents():
    early = _document(document_id="early")
    late = _document(
        document_id="late",
        published_at="2026-08-10T15:01:00+08:00",
    )
    context_a = build_extraction_context([late, early])
    context_b = build_extraction_context([early, late])

    assert context_a["context_hash"] == context_b["context_hash"]

    factors = aggregate_sentiment(
        [early, late],
        [_extraction("early", 1), _extraction("late", -1)],
        as_of="2026-08-10",
        cutoff="15:00",
        target="512890",
        horizon=5,
    )
    assert factors["news_event_sentiment"]["value"] == pytest.approx(100.0)
    assert factors["news_event_sentiment"]["count"] == 1


def test_news_and_blogger_series_are_separate_and_performance_weighted():
    news = _document(document_id="news", document_type="news")
    equal = _document(
        document_id="equal", document_type="blogger", author_id="author-a"
    )
    weighted = _document(
        document_id="weighted", document_type="blogger", author_id="author-b"
    )
    extractions = [
        _extraction("news", 1),
        _extraction("equal", 1),
        _extraction("weighted", -1),
    ]

    factors = aggregate_sentiment(
        [news, equal, weighted],
        extractions,
        as_of="2026-08-10",
        cutoff="15:00",
        target="512890",
        horizon=5,
        author_weights={"author-a": 0.5, "author-b": 1.5},
    )

    assert factors["news_event_sentiment"]["value"] == pytest.approx(100.0)
    assert factors["blogger_consensus_equal"]["value"] == pytest.approx(0.0)
    assert factors["blogger_consensus_performance_weighted"]["value"] < 0


def test_missing_content_is_not_neutral_and_percentile_requires_history():
    factors = aggregate_sentiment(
        [], [], as_of="2026-08-10", cutoff="15:00", target="512890", horizon=5
    )

    assert factors["news_event_sentiment"]["status"] == "missing"
    assert factors["news_event_sentiment"]["value"] is None
    assert rolling_percentile(50, [10, 20, 30]) is None
    assert rolling_percentile(10, list(range(20))) == pytest.approx(0.55)


def test_invalid_extraction_direction_is_rejected():
    with pytest.raises(ValueError, match="direction"):
        normalize_extraction(
            "doc-1",
            {"claims": [{"direction": 2}]},
            model="fixture",
            prompt_version="v1",
        )


def test_snapshot_contains_all_horizons_and_backtest_coverage_gate():
    document = _document(document_id="history")
    extraction = _extraction("history", 1, horizon=1)
    snapshot = build_sentiment_snapshot(
        [document],
        [extraction],
        snapshot_date="2026-08-10",
        cutoff="15:00",
        scope_type="instrument",
        scope_key="512890",
        trading_dates=[
            (date(2026, 7, 22) + timedelta(days=index)).isoformat()
            for index in range(20)
        ],
    )

    assert set(snapshot["factors"]) == {"1d", "5d", "20d"}
    assert snapshot["factors"]["1d"]["news_event_sentiment"]["value"] == pytest.approx(100.0)
    assert snapshot["backtest_eligibility"]["status"] == "exploratory_only"
    assert snapshot["backtest_eligibility"]["coverage"] < 0.5


def test_author_performance_does_not_use_unfinished_forward_window():
    document = _document(document_id="opinion", document_type="blogger", author_id="a")
    extraction = _extraction("opinion", 1, horizon=5)

    performance = evaluate_author_performance(
        [document],
        [extraction],
        returns_by_target={"512890": {"2026-08-11": 0.02}},
        benchmark_returns_by_target={"512890": {"2026-08-11": 0.01}},
        as_of="2026-08-12",
    )

    assert performance[0]["sample_count"] == 0
    assert performance[0]["status"] == "insufficient_sample"
