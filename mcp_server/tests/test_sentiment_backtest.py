from mcp_server.services.sentiment_backtest import attach_sentiment_snapshots


def test_sentiment_snapshots_are_date_aligned_without_future_leakage():
    data = {
        "512890": [
            {"date": "2026-08-10", "close": 1.0},
            {"date": "2026-08-11", "close": 1.1},
        ]
    }
    snapshots = [
        {
            "snapshot_date": "2026-08-11",
            "cutoff": "15:00",
            "scope": {"type": "instrument", "key": "512890"},
            "factors": {"5d": {"news_event_sentiment": {"value": 80}}},
        },
        {
            "snapshot_date": "2026-08-10",
            "cutoff": "15:00",
            "scope": {"type": "instrument", "key": "512890"},
            "factors": {"5d": {"news_event_sentiment": {"value": 20}}},
        },
    ]

    result = attach_sentiment_snapshots(data, snapshots)

    assert result["512890"][0]["sentiment_factors"]["news_event_sentiment"]["value"] == 20
    assert result["512890"][1]["sentiment_factors"]["news_event_sentiment"]["value"] == 80


def test_sentiment_snapshots_do_not_mutate_original_bars():
    bars = [{"date": "2026-08-10", "close": 1.0}]
    data = {"512890": bars}
    result = attach_sentiment_snapshots(data, [])

    assert "sentiment_factors" not in bars[0]
    assert result["512890"][0]["sentiment_factors"] == {}
