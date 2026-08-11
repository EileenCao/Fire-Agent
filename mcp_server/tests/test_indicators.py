from datetime import date, timedelta

from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.indicators import build_indicator_series, wilder_rsi


def test_wilder_rsi_is_warmed_up_after_period_changes():
    values = wilder_rsi([1, 2, 3, 4, 5, 6, 7], period=3)

    assert values[:3] == [None, None, None]
    assert values[3:] == [100.0, 100.0, 100.0, 100.0]


def test_wilder_rsi_returns_zero_for_a_continuous_decline():
    values = wilder_rsi([7, 6, 5, 4, 3, 2, 1], period=3)

    assert values[3:] == [0.0, 0.0, 0.0, 0.0]


def test_weekly_rsi_uses_previous_completed_week_until_week_end():
    bars = []
    start = date(2026, 1, 5)
    weekly_closes = list(range(1, 16)) + [14]
    for week_index, weekly_close in enumerate(weekly_closes):
        week_start = start + timedelta(days=week_index * 7)
        for day_offset in range(5):
            bars.append(
                {
                    "date": (week_start + timedelta(days=day_offset)).isoformat(),
                    "open": weekly_close,
                    "high": weekly_close,
                    "low": weekly_close,
                    "close": weekly_close,
                }
            )

    spec = StrategySpec.from_dict(
        {
            "strategy_id": "weekly-indicator",
            "version": "1.0.0",
            "name": "weekly indicator",
            "universe": ["512890"],
            "frequency": "1d",
            "entry": {"rules": []},
            "exit": {"rules": []},
            "position_sizing": {"type": "all_in"},
            "indicators": [
                {
                    "id": "weekly_rsi_14",
                    "type": "rsi",
                    "timeframe": "1w",
                    "period": 14,
                    "source": "close",
                    "method": "wilder",
                    "completed_only": True,
                }
            ],
        }
    )

    series = build_indicator_series(spec, bars)["weekly_rsi_14"]

    monday_of_last_week = 15 * 5
    friday_of_last_week = monday_of_last_week + 4
    assert series[monday_of_last_week] == 100.0
    assert series[friday_of_last_week] < 100.0


def test_sentiment_indicator_reads_date_aligned_factor_from_bar():
    spec = StrategySpec.from_dict(
        {
            "strategy_id": "sentiment-indicator",
            "version": "1.0.0",
            "name": "sentiment indicator",
            "universe": ["512890"],
            "frequency": "1d",
            "entry": {"rules": []},
            "exit": {"rules": []},
            "position_sizing": {"type": "all_in"},
            "indicators": [
                {
                    "id": "news_sentiment_5d",
                    "type": "sentiment",
                    "factor": "news_event_sentiment",
                    "scope": "instrument",
                    "horizon": 5,
                    "representation": "percentile",
                    "cutoff": "15:00",
                    "profile": "sentiment-baseline-v1",
                }
            ],
        }
    )
    bars = [
        {
            "date": "2026-08-10",
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "sentiment_factors": {"news_event_sentiment": {"percentile": 0.75}},
        }
    ]

    assert build_indicator_series(spec, bars)["news_sentiment_5d"] == [0.75]
