from datetime import date

import pytest

from mcp_server.domain.strategy import StrategySpec, validate_run_assumptions
from mcp_server.services.performance import (
    calculate_benchmark_metrics,
    calculate_performance_metrics,
)


def _strategy_payload(**overrides):
    payload = {
        "strategy_id": "demo-strategy",
        "version": "1.0.0",
        "name": "演示策略",
        "universe": ["512890"],
        "frequency": "1d",
        "entry": {"rules": []},
        "exit": {"rules": []},
        "position_sizing": {"type": "all_in"},
        "benchmark": None,
        "risk_free_rate_annual": 0.0,
    }
    payload.update(overrides)
    return payload


def test_strategy_preserves_explicit_benchmark_and_risk_free_rate():
    spec = StrategySpec.from_dict(
        _strategy_payload(
            benchmark={
                "code": "000300",
                "market": "SH",
                "instrument_type": "INDEX",
                "name": "沪深300",
            },
            risk_free_rate_annual=0.018,
        )
    )

    assert spec.benchmark["market"] == "SH"
    assert spec.risk_free_rate_annual == pytest.approx(0.018)
    assert spec.to_dict()["benchmark"]["code"] == "000300"


def test_run_assumptions_require_explicit_values_and_confirmation():
    errors = validate_run_assumptions(
        _strategy_payload(), confirm_benchmark=False, confirm_risk_free_rate=False
    )

    assert errors == [
        "回测前必须确认 benchmark 选择",
        "回测前必须确认年化无风险利率",
    ]
    assert validate_run_assumptions(
        _strategy_payload(), confirm_benchmark=True, confirm_risk_free_rate=True
    ) == []


def test_run_assumptions_reject_missing_or_null_risk_free_rate():
    missing = _strategy_payload()
    missing.pop("risk_free_rate_annual")
    null_value = _strategy_payload(risk_free_rate_annual=None)

    assert "策略必须显式设置 risk_free_rate_annual" in validate_run_assumptions(
        missing, True, True
    )
    assert "risk_free_rate_annual 不能为 null" in validate_run_assumptions(
        null_value, True, True
    )


def test_performance_metrics_include_annualized_return_and_risk_ratios():
    metrics = calculate_performance_metrics(
        initial=100.0,
        equity_curve={
            "2024-01-01": 100.0,
            "2024-01-02": 101.0,
            "2024-01-03": 99.0,
            "2024-01-04": 105.0,
        },
        trades=[],
        cash_flows=[],
        risk_free_rate_annual=0.0,
    )

    assert metrics["annualized_return"] is not None
    assert metrics["annualized_volatility"] is not None
    assert metrics["max_drawdown"] == pytest.approx(0.01980198, rel=1e-6)
    assert metrics["max_drawdown_peak_date"] == "2024-01-02"
    assert metrics["max_drawdown_trough_date"] == "2024-01-03"
    assert metrics["sharpe_ratio"] is not None
    assert metrics["sortino_ratio"] is not None
    assert metrics["calmar_ratio"] is not None


def test_performance_metrics_exclude_idle_cash_from_invested_return():
    metrics = calculate_performance_metrics(
        initial=1000.0,
        equity_curve={
            "2024-01-01": 1000.0,
            "2024-01-02": 1000.0,
            "2024-01-03": 1100.0,
            "2024-01-04": 1200.0,
        },
        trades=[
            {
                "date": "2024-01-02",
                "side": "BUY",
                "status": "FILLED",
                "price": 5.0,
                "quantity": 100,
                "fee": 0.0,
            },
            {
                "date": "2024-01-04",
                "side": "SELL",
                "status": "FILLED",
                "price": 7.0,
                "quantity": 50,
                "fee": 0.0,
            },
        ],
        cash_flows=[],
        market_value_curve={
            "2024-01-01": 0.0,
            "2024-01-02": 500.0,
            "2024-01-03": 600.0,
            "2024-01-04": 350.0,
        },
    )

    assert metrics["cumulative_return"] == pytest.approx(0.2)
    assert metrics["cash_neutral_cumulative_return"] == pytest.approx(0.4)
    assert metrics["cash_neutral_active_sessions"] == 2
    assert metrics["cash_neutral_annualized_return"] == pytest.approx(
        1.4 ** (365.25 / 2) - 1.0
    )


def test_cash_neutral_twr_and_active_drawdown_exclude_idle_cash():
    metrics = calculate_performance_metrics(
        initial=1000.0,
        equity_curve={
            "2024-01-01": 1000.0,
            "2024-01-02": 1000.0,
            "2024-01-03": 900.0,
            "2024-01-04": 1100.0,
        },
        trades=[
            {
                "date": "2024-01-02",
                "side": "BUY",
                "status": "FILLED",
                "price": 5.0,
                "quantity": 100,
                "fee": 0.0,
            },
            {
                "date": "2024-01-04",
                "side": "SELL",
                "status": "FILLED",
                "price": 6.0,
                "quantity": 100,
                "fee": 0.0,
            },
        ],
        cash_flows=[],
        market_value_curve={
            "2024-01-01": 0.0,
            "2024-01-02": 500.0,
            "2024-01-03": 400.0,
            "2024-01-04": 0.0,
        },
    )

    assert metrics["cash_neutral_twr_cumulative_return"] == pytest.approx(0.2)
    assert metrics["cash_neutral_twr_annualized_return"] == pytest.approx(
        1.2 ** (365.25 / 3) - 1.0
    )
    assert metrics["cash_neutral_active_calendar_days"] == 3
    assert metrics["cash_neutral_max_drawdown"] == pytest.approx(0.2)
    assert metrics["cash_neutral_max_drawdown_peak_date"] == "2024-01-02"
    assert metrics["cash_neutral_max_drawdown_trough_date"] == "2024-01-03"
    assert metrics["cash_neutral_max_drawdown_recovery_date"] == "2024-01-04"


def test_performance_metrics_expose_period_returns_and_trade_quality():
    metrics = calculate_performance_metrics(
        initial=1000.0,
        equity_curve={
            "2024-01-31": 1100.0,
            "2024-02-29": 990.0,
            "2024-03-29": 1200.0,
        },
        trades=[
            {"side": "SELL", "status": "FILLED", "pnl": 100, "price": 11, "quantity": 100, "fee": 2},
            {"side": "SELL", "status": "FILLED", "pnl": -50, "price": 10, "quantity": 100, "fee": 2},
            {"side": "BUY", "status": "FILLED", "price": 10, "quantity": 100, "fee": 2},
            {"side": "SELL", "status": "UNFILLED", "pnl": 999, "price": 10, "quantity": 100},
        ],
        cash_flows=[],
        risk_free_rate_annual=0.0,
    )

    assert metrics["profit_factor"] == pytest.approx(2.0)
    assert metrics["payoff_ratio"] == pytest.approx(2.0)
    assert metrics["realized_sell_count"] == 2
    assert metrics["unfilled_order_count"] == 1
    assert metrics["total_fees"] == pytest.approx(6.0)
    assert metrics["monthly_returns"]["2024-02"] == pytest.approx(-0.1)


def test_benchmark_metrics_align_dates_and_calculate_relative_statistics():
    comparison = calculate_benchmark_metrics(
        strategy_equity={"2024-01-01": 100, "2024-01-02": 102, "2024-01-03": 104},
        benchmark_equity={"2024-01-01": 100, "2024-01-02": 101, "2024-01-03": 102},
        risk_free_rate_annual=0.0,
    )

    assert comparison["status"] == "ok"
    assert comparison["coverage"] == pytest.approx(1.0)
    assert comparison["strategy_return"] == pytest.approx(0.04)
    assert comparison["benchmark_return"] == pytest.approx(0.02)
    assert comparison["excess_return"] == pytest.approx(0.02)
    assert comparison["tracking_error"] is not None
    assert comparison["beta"] is not None


def test_benchmark_metrics_report_unavailable_when_dates_do_not_overlap():
    comparison = calculate_benchmark_metrics(
        strategy_equity={"2024-01-01": 100},
        benchmark_equity={"2024-02-01": 100},
    )

    assert comparison["status"] == "unavailable"
    assert "日期" in comparison["reason"]


def test_performance_metrics_separate_fees_and_exposure_and_track_recovery():
    metrics = calculate_performance_metrics(
        initial=1000.0,
        equity_curve={
            "2024-01-01": 1000.0,
            "2024-01-02": 900.0,
            "2024-01-03": 1000.0,
        },
        trades=[
            {
                "side": "BUY",
                "status": "FILLED",
                "price": 10,
                "raw_price": 9.9,
                "quantity": 100,
                "fee": 2,
                "commission": 1,
                "stamp_duty": 0,
                "transfer_fee": 1,
                "slippage_impact": 10,
            }
        ],
        cash_flows=[],
        risk_free_rate_annual=0.0,
        cash_curve={"2024-01-01": 1000, "2024-01-02": 400, "2024-01-03": 1000},
        market_value_curve={"2024-01-01": 0, "2024-01-02": 500, "2024-01-03": 0},
    )

    assert metrics["commission"] == pytest.approx(1.0)
    assert metrics["transfer_fee"] == pytest.approx(1.0)
    assert metrics["stamp_duty"] == pytest.approx(0.0)
    assert metrics["slippage_impact"] == pytest.approx(10.0)
    assert metrics["max_drawdown_recovery_date"] == "2024-01-03"
    assert metrics["average_exposure"] == pytest.approx(5 / 27, rel=1e-6)
    assert metrics["max_exposure"] == pytest.approx(500 / 900, rel=1e-6)


def test_external_cash_flow_does_not_become_time_weighted_return():
    metrics = calculate_performance_metrics(
        initial=100.0,
        equity_curve={"2024-01-01": 100.0, "2024-01-02": 200.0},
        trades=[],
        cash_flows=[{"date": "2024-01-02", "amount": 100.0}],
        risk_free_rate_annual=0.0,
    )

    assert metrics["net_profit"] == pytest.approx(0.0)
    assert metrics["time_weighted_return"] == pytest.approx(0.0)
