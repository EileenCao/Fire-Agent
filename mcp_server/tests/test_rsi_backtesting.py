from datetime import date, timedelta

from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtesting import BacktestEngine


def _close_rsi_spec():
    return StrategySpec.from_dict(
        {
            "strategy_id": "etf-512890-rsi-timing",
            "version": "1.0.0",
            "name": "512890 RSI 定投择时",
            "universe": ["512890"],
            "frequency": "1d",
            "indicators": [
                {
                    "id": "daily_rsi_6",
                    "type": "rsi",
                    "timeframe": "1d",
                    "period": 6,
                    "source": "close",
                    "method": "wilder",
                }
            ],
            "entry": {
                "mode": "count_conditions",
                "conditions": [
                    {"id": "daily_low", "indicator": "daily_rsi_6", "operator": "<", "value": 30}
                ],
                "amount_by_count": {"1": 500, "2": 1000},
            },
            "exit": {
                "mode": "count_conditions",
                "conditions": [
                    {"id": "daily_high", "indicator": "daily_rsi_6", "operator": ">", "value": 80}
                ],
                "fraction_by_count": {"1": 0.2, "2": 1 / 3, "3": 1.0},
            },
            "position_sizing": {"type": "recurrent_cash", "lot_size": 100, "initial_quantity": 0},
            "initial_capital": 50000,
            "cost_profile": {
                "template": "theoretical",
                "version": "1.0.0",
                "commission_rate": 0,
                "stamp_duty_rate": 0,
                "transfer_fee_rate": 0,
                "slippage_rate": 0,
                "minimum_commission": 0,
            },
            "execution": {"signal_at": "close", "fill_at": "close", "action_priority": ["EXIT", "ENTRY"]},
        }
    )


def _full_exit_spec():
    return StrategySpec.from_dict(
        {
            "strategy_id": "full-exit-fixture",
            "version": "1.0.0",
            "name": "full exit fixture",
            "universe": ["512890"],
            "frequency": "1d",
            "indicators": [
                {"id": "rsi_a", "type": "rsi", "timeframe": "1d", "period": 2, "source": "close", "method": "wilder"},
                {"id": "rsi_b", "type": "rsi", "timeframe": "1d", "period": 2, "source": "close", "method": "wilder"},
                {"id": "rsi_c", "type": "rsi", "timeframe": "1d", "period": 2, "source": "close", "method": "wilder"},
            ],
            "entry": {
                "mode": "count_conditions",
                "conditions": [{"id": "entry", "indicator": "rsi_a", "operator": "<", "value": 30}],
                "amount_by_count": {"1": 500, "2": 1000},
            },
            "exit": {
                "mode": "count_conditions",
                "conditions": [
                    {"id": "exit_a", "indicator": "rsi_a", "operator": ">", "value": 80},
                    {"id": "exit_b", "indicator": "rsi_b", "operator": ">", "value": 80},
                    {"id": "exit_c", "indicator": "rsi_c", "operator": ">", "value": 80},
                ],
                "fraction_by_count": {"1": 0.2, "2": 1 / 3, "3": 1.0},
            },
            "position_sizing": {"type": "recurrent_cash", "lot_size": 100, "initial_quantity": 0},
            "initial_capital": 50000,
            "execution": {"signal_at": "close", "fill_at": "close", "action_priority": ["EXIT", "ENTRY"]},
        }
    )


def _bars(closes):
    start = date(2026, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": 100.0,
            "high": max(100.0, float(close)),
            "low": min(100.0, float(close)),
            "close": float(close),
        }
        for index, close in enumerate(closes)
    ]


def _scenario(spec, closes):
    result = BacktestEngine().run(spec, {"512890": _bars(closes)})
    return result["scenarios"]["default"]


def test_close_policy_fills_on_signal_bar_close():
    result = BacktestEngine().run(_close_rsi_spec(), {"512890": _bars([10, 9, 8, 7, 6, 5, 4, 3])})
    scenario = result["scenarios"]["default"]

    trade = next(item for item in scenario["trades"] if item["side"] == "BUY")
    assert trade["signal_date"] == trade["date"] == "2026-01-07"
    assert trade["price"] == 4
    assert trade["price"] != 100
    assert result["provenance"]["execution"]["fill_at"] == "close"


def test_close_policy_repeats_buy_while_holding():
    scenario = _scenario(_close_rsi_spec(), [10, 9, 8, 7, 6, 5, 4, 3])

    buys = [item for item in scenario["trades"] if item["side"] == "BUY"]
    assert [item["quantity"] for item in buys] == [100, 100]
    assert [item["position_after"] for item in buys] == [100, 200]
    assert all(item["date"] == item["signal_date"] for item in buys)
    assert all(item["cash_after"] >= 0 for item in buys)


def test_close_policy_reports_exact_suspension_and_terminal_position_warnings():
    bars = _bars([10, 9, 8, 7, 6, 5, 4, 3])
    bars[-1]["suspended"] = True

    result = BacktestEngine().run(_close_rsi_spec(), {"512890": bars})
    warnings = result["scenarios"]["default"]["warnings"]

    assert "512890 2026-01-08 停牌，无法产生或执行交易" in warnings
    assert "512890 数据结束时仍有未平仓头寸，按最后收盘价估值" in warnings


def test_one_condition_sell_is_a_rounded_partial_sale():
    closes = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2] + list(range(3, 20))
    scenario = _scenario(_close_rsi_spec(), closes)

    sell = next(item for item in scenario["trades"] if item["side"] == "SELL")
    assert sell["quantity"] == 100
    assert sell["position_after"] == sell["position_before"] - 100
    assert sell["signal_date"] == sell["date"]


def test_three_exit_conditions_liquidate_all_remaining_lots():
    scenario = _scenario(_full_exit_spec(), [4, 3, 2, 1, 2, 3, 4, 5])

    sell = next(item for item in scenario["trades"] if item["side"] == "SELL")
    assert sell["quantity"] == sell["position_before"]
    assert sell["position_after"] == 0
