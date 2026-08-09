from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtest_pipeline import enrich_backtest_result


def _spec(benchmark=None):
    return StrategySpec.from_dict(
        {
            "strategy_id": "benchmark-test",
            "version": "1.0.0",
            "name": "benchmark test",
            "universe": ["512890"],
            "frequency": "1d",
            "entry": {"rules": []},
            "exit": {"rules": []},
            "position_sizing": {"type": "all_in"},
            "benchmark": benchmark,
            "risk_free_rate_annual": 0.02,
        }
    )


def _data():
    return {
        "000300": [
            {"date": "2026-01-01", "close": 100},
            {"date": "2026-01-02", "close": 102},
            {"date": "2026-01-03", "close": 101},
        ]
    }


def test_enrich_result_marks_no_benchmark_as_explicitly_not_selected():
    result = {"scenarios": {"default": {"equity_curve": {"2026-01-01": 100, "2026-01-02": 101}}}}

    enriched = enrich_backtest_result(result, _spec())

    assert enriched["benchmark"]["status"] == "not_selected"
    assert enriched["benchmark_comparison"]["status"] == "not_selected"


def test_enrich_result_computes_relative_metrics_from_benchmark_bars():
    benchmark = {
        "code": "000300",
        "market": "SH",
        "instrument_type": "INDEX",
        "name": "沪深300",
    }
    result = {"scenarios": {"default": {"equity_curve": {"2026-01-01": 100, "2026-01-02": 104, "2026-01-03": 103}}}}

    enriched = enrich_backtest_result(result, _spec(benchmark), benchmark_data=_data())

    assert enriched["benchmark"]["status"] == "ok"
    assert enriched["benchmark_comparison"]["status"] == "ok"
    assert enriched["benchmark_comparison"]["coverage"] == 1.0
    assert enriched["benchmark_comparison"]["benchmark_return"] == 0.01


def test_enrich_result_degrades_when_benchmark_data_is_missing():
    benchmark = {
        "code": "000300",
        "market": "SH",
        "instrument_type": "INDEX",
        "name": "沪深300",
    }
    result = {"scenarios": {"default": {"equity_curve": {"2026-01-01": 100, "2026-01-02": 104}}}}

    enriched = enrich_backtest_result(
        result,
        _spec(benchmark),
        benchmark_errors={"000300": "fixture unavailable"},
    )

    assert enriched["benchmark"]["status"] == "unavailable"
    assert enriched["benchmark_comparison"]["status"] == "unavailable"
    assert "fixture unavailable" in enriched["benchmark_comparison"]["reason"]
