from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.signal_planner import build_signal_plan


def _rsi_spec():
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
                },
                {
                    "id": "daily_rsi_12",
                    "type": "rsi",
                    "timeframe": "1d",
                    "period": 12,
                    "source": "close",
                    "method": "wilder",
                },
                {
                    "id": "weekly_rsi_14",
                    "type": "rsi",
                    "timeframe": "1w",
                    "period": 14,
                    "source": "close",
                    "method": "wilder",
                    "completed_only": True,
                },
            ],
            "entry": {
                "mode": "count_conditions",
                "conditions": [
                    {"id": "daily_low", "indicator": "daily_rsi_6", "operator": "<", "value": 30},
                    {"id": "weekly_low", "indicator": "weekly_rsi_14", "operator": "<", "value": 47},
                ],
                "amount_by_count": {"1": 500, "2": 1000},
            },
            "exit": {
                "mode": "count_conditions",
                "conditions": [
                    {"id": "daily_high", "indicator": "daily_rsi_6", "operator": ">", "value": 80},
                    {"id": "daily_mid_high", "indicator": "daily_rsi_12", "operator": ">", "value": 70},
                    {"id": "weekly_high", "indicator": "weekly_rsi_14", "operator": ">", "value": 63},
                ],
                "fraction_by_count": {"1": 0.2, "2": 1 / 3, "3": 1.0},
            },
            "position_sizing": {"type": "recurrent_cash", "lot_size": 100, "initial_quantity": 0},
            "initial_capital": 50000,
            "execution": {"signal_at": "close", "fill_at": "close", "action_priority": ["EXIT", "ENTRY"]},
        }
    )


def _bar():
    return {"date": "2026-01-05", "open": 10, "high": 10, "low": 10, "close": 10}


def test_exit_has_priority_and_skips_entry():
    indicators = {
        "daily_rsi_6": [85.0],
        "daily_rsi_12": [75.0],
        "weekly_rsi_14": [65.0],
    }

    plan = build_signal_plan(_rsi_spec(), [_bar()], 0, quantity=500, indicator_series=indicators)

    assert plan["action"] == "SELL"
    assert plan["sell_quantity"] == 500
    assert plan["buy_cash"] == 0


def test_two_buy_conditions_request_double_cash_while_already_holding():
    indicators = {
        "daily_rsi_6": [25.0],
        "daily_rsi_12": [50.0],
        "weekly_rsi_14": [40.0],
    }

    plan = build_signal_plan(_rsi_spec(), [_bar()], 0, quantity=100, indicator_series=indicators)

    assert plan["action"] == "BUY"
    assert plan["buy_cash"] == 1000


def test_one_partial_sell_rounds_down_to_lot_size():
    indicators = {
        "daily_rsi_6": [85.0],
        "daily_rsi_12": [50.0],
        "weekly_rsi_14": [50.0],
    }

    plan = build_signal_plan(_rsi_spec(), [_bar()], 0, quantity=550, indicator_series=indicators)

    assert plan["action"] == "SELL"
    assert plan["sell_quantity"] == 100


def test_exit_signal_without_position_blocks_a_new_buy():
    indicators = {
        "daily_rsi_6": [85.0],
        "daily_rsi_12": [50.0],
        "weekly_rsi_14": [40.0],
    }

    plan = build_signal_plan(_rsi_spec(), [_bar()], 0, quantity=0, indicator_series=indicators)

    assert plan["action"] == "HOLD"
    assert plan["sell_quantity"] == 0
    assert plan["evidence"]["reason"] == "EXIT_WITHOUT_POSITION"
