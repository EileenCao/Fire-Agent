from mcp_server.cli import _normalize_backtest_data


def test_normalize_backtest_data_accepts_cached_single_symbol_wrapper():
    bars = [{"date": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1}]
    data, provenance = _normalize_backtest_data(
        {"code": "512890", "bars": bars, "provenance": {"bar_count": 1}}
    )

    assert data == {"512890": bars}
    assert provenance == {"bar_count": 1}
