from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtesting import BacktestEngine


def _bars(values, opens=None):
    opens = opens or values
    return [
        {
            "date": "2026-01-{:02d}".format(index + 1),
            "open": opens[index],
            "high": max(opens[index], values[index]),
            "low": min(opens[index], values[index]),
            "close": values[index],
        }
        for index in range(len(values))
    ]


def _strategy(**overrides):
    payload = {
        "strategy_id": "position-management",
        "version": "1.0.0",
        "name": "持仓管理测试",
        "universe": ["512890"],
        "frequency": "1d",
        "entry": {"rules": [{"type": "state", "left": "close", "right": 0}]},
        "exit": {"rules": []},
        "position_sizing": {
            "type": "fixed_quantity",
            "quantity": 100,
            "lot_size": 100,
        },
        "initial_capital": 5000,
        "cost_profile": {"commission_rate": 0.0, "slippage_rate": 0.0},
    }
    payload.update(overrides)
    return StrategySpec.from_dict(payload)


def _scenario(spec, values, opens=None, symbols=None):
    data = symbols or {"512890": _bars(values, opens)}
    return BacktestEngine().run(spec, data)["scenarios"]["default"]


def test_strategy_spec_accepts_position_management_configuration():
    spec = _strategy(
        action_priority=["SELL", "PERIODIC_BUY", "SIGNAL_BUY"],
        position_sizing={
            "capital_scope": "per_symbol",
            "type": "fixed_cash",
            "cash": 1000,
            "lot_size": 100,
            "while_holding": {
                "signal_add": {"enabled": True, "type": "fixed_cash", "amount": 1000},
                "periodic": {
                    "enabled": True,
                    "frequency": "dates",
                    "dates": ["2026-01-04"],
                    "type": "fixed_cash",
                    "amount": 1000,
                    "funding": "external_contribution",
                    "non_trading_day": "next_trading_day",
                    "execution": "scheduled_open",
                },
            },
        },
        exit={"rules": [], "sell": {"type": "percent", "value": 0.5}},
    )

    assert spec.is_valid is True
    assert spec.action_priority == ["SELL", "PERIODIC_BUY", "SIGNAL_BUY"]


def test_strategy_spec_rejects_ambiguous_execution_and_invalid_sell_quantity():
    spec = _strategy(
        position_sizing={
            "type": "fixed_quantity",
            "quantity": 100,
            "lot_size": 100,
            "while_holding": {
                "periodic": {
                    "enabled": True,
                    "frequency": "monthly",
                    "day": 1,
                    "type": "fixed_cash",
                    "amount": 100,
                    "funding": "existing_cash",
                    "non_trading_day": "skip",
                    "execution": "strategy_configured",
                }
            },
        },
        exit={"rules": [], "sell": {"type": "quantity", "value": 55}},
    )

    assert spec.is_valid is False
    assert any("execution" in error or "成交" in error for error in spec.validation_errors)
    assert any("交易单位" in error or "数量" in error for error in spec.validation_errors)


def test_periodic_dca_adds_external_cash_while_holding():
    spec = _strategy(
        entry={"rules": [{"type": "state", "left": "close", "right": 0}]},
        position_sizing={
            "type": "fixed_quantity",
            "quantity": 100,
            "lot_size": 100,
            "while_holding": {
                "periodic": {
                    "enabled": True,
                    "frequency": "dates",
                    "dates": ["2026-01-03"],
                    "type": "fixed_quantity",
                    "quantity": 100,
                    "amount": 1000,
                    "funding": "external_contribution",
                    "non_trading_day": "next_trading_day",
                    "execution": "scheduled_open",
                }
            },
        },
    )

    scenario = _scenario(spec, [1, 1, 1, 1], opens=[10, 10, 10, 10])

    buys = [trade for trade in scenario["trades"] if trade["side"] == "BUY"]
    assert [trade["quantity"] for trade in buys] == [100, 100]
    assert buys[1]["source"] == "PERIODIC_BUY"
    assert scenario["metrics"]["external_cash_flow"] == 1000
    assert scenario["metrics"]["total_contributed"] == 6000


def test_signal_add_and_fifo_t1_allow_partial_sell_of_old_lot_only():
    spec = _strategy(
        action_priority=["SIGNAL_BUY", "SELL"],
        entry={"rules": [{"type": "state", "left": "close", "right": 0}]},
        exit={"rules": [{"type": "state", "left": "close", "right": -0.5}], "sell": {"type": "percent", "value": 0.5}},
        position_sizing={
            "type": "fixed_quantity",
            "quantity": 100,
            "lot_size": 100,
            "while_holding": {
                "signal_add": {"enabled": True, "type": "fixed_quantity", "quantity": 100, "amount": 1000}
            },
        },
    )

    scenario = _scenario(spec, [1, 1, -1, -1, -1], opens=[10, 10, 10, 10, 10])

    buys = [trade for trade in scenario["trades"] if trade["side"] == "BUY"]
    sells = [trade for trade in scenario["trades"] if trade["side"] == "SELL"]
    assert len(buys) == 2
    assert buys[1]["source"] == "SIGNAL_BUY"
    assert sells[0]["quantity"] == 100
    assert sells[0]["position_before"] == 200
    assert sells[0]["position_after"] == 100
    assert sells[0]["lot_ids"] == [buys[0]["lot_id"]]
    assert sells[0]["date"] == "2026-01-03"


def test_fixed_quantity_sell_is_clipped_to_available_position_with_warning():
    spec = _strategy(
        exit={"rules": [{"type": "state", "left": "close", "right": -1}], "sell": {"type": "quantity", "value": 300}}
    )

    scenario = _scenario(spec, [1, 1, -1, -1], opens=[10, 10, 10, 10])

    sell = next(trade for trade in scenario["trades"] if trade["side"] == "SELL")
    assert sell["requested_quantity"] == 300
    assert sell["quantity"] == 100
    assert sell["status"] == "PARTIAL"
    assert any("可卖" in warning or "截断" in warning for warning in scenario["warnings"])


def test_portfolio_scope_shares_cash_between_symbols():
    spec = _strategy(
        universe=["512890", "600000"],
        initial_capital=1000,
        position_sizing={
            "capital_scope": "portfolio",
            "type": "fixed_cash",
            "cash": 1000,
            "lot_size": 100,
        },
    )
    symbols = {
        "512890": _bars([1, 1], opens=[10, 10]),
        "600000": _bars([1, 1], opens=[10, 10]),
    }

    scenario = _scenario(spec, [1, 1], symbols=symbols)

    buys = [trade for trade in scenario["trades"] if trade["side"] == "BUY"]
    assert sum(trade.get("quantity", 0) for trade in buys) == 100
    assert any(trade.get("status") == "UNFILLED" for trade in scenario["trades"])


def test_periodic_plan_can_skip_or_roll_forward_a_non_trading_date():
    base = {
        "enabled": True,
        "frequency": "dates",
        "dates": ["2026-01-03"],
        "type": "fixed_quantity",
        "quantity": 100,
        "amount": 1000,
        "funding": "external_contribution",
        "execution": "scheduled_open",
    }
    bars = [
        {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 1},
        {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 1},
        {"date": "2026-01-04", "open": 10, "high": 10, "low": 10, "close": 1},
    ]

    next_spec = _strategy(
        position_sizing={
            "type": "fixed_quantity",
            "quantity": 100,
            "lot_size": 100,
            "while_holding": {"periodic": dict(base, non_trading_day="next_trading_day")},
        }
    )
    skip_spec = _strategy(
        position_sizing={
            "type": "fixed_quantity",
            "quantity": 100,
            "lot_size": 100,
            "while_holding": {"periodic": dict(base, non_trading_day="skip")},
        }
    )

    next_trades = BacktestEngine().run(next_spec, {"512890": bars})["scenarios"]["default"]["trades"]
    skip_trades = BacktestEngine().run(skip_spec, {"512890": bars})["scenarios"]["default"]["trades"]

    assert [trade for trade in next_trades if trade["source"] == "PERIODIC_BUY"][0]["date"] == "2026-01-04"
    assert not [trade for trade in skip_trades if trade["source"] == "PERIODIC_BUY"]


def test_periodic_next_open_uses_the_bar_after_the_planned_date():
    spec = _strategy(
        position_sizing={
            "type": "fixed_quantity",
            "quantity": 100,
            "lot_size": 100,
            "while_holding": {
                "periodic": {
                    "enabled": True,
                    "frequency": "dates",
                    "dates": ["2026-01-03"],
                    "type": "fixed_quantity",
                    "quantity": 100,
                    "amount": 1000,
                    "funding": "external_contribution",
                    "non_trading_day": "skip",
                    "execution": "next_open",
                }
            },
        }
    )

    trades = _scenario(spec, [1, 1, 1, 1], opens=[10, 10, 10, 10])["trades"]
    periodic = next(trade for trade in trades if trade["source"] == "PERIODIC_BUY")

    assert periodic["date"] == "2026-01-04"
    assert periodic["signal_date"] == "2026-01-03"


def test_same_day_order_priority_controls_cash_reuse():
    common = {
        "entry": {"rules": [{"type": "state", "left": "close", "right": 0}]},
        "exit": {"rules": [{"type": "state", "left": "close", "right": -0.5, "operator": "<"}]},
        "position_sizing": {
            "type": "fixed_quantity",
            "quantity": 100,
            "lot_size": 100,
            "while_holding": {
                "periodic": {
                    "enabled": True,
                    "frequency": "dates",
                    "dates": ["2026-01-03"],
                    "type": "fixed_quantity",
                    "quantity": 100,
                    "funding": "existing_cash",
                    "non_trading_day": "skip",
                    "execution": "scheduled_open",
                }
            },
        },
        "initial_capital": 1000,
    }
    sell_first = _strategy(**dict(common, action_priority=["SELL", "PERIODIC_BUY"]))
    buy_first = _strategy(**dict(common, action_priority=["PERIODIC_BUY", "SELL"]))

    sell_first_trades = _scenario(sell_first, [1, -1, -1], opens=[10, 10, 10])["trades"]
    buy_first_trades = _scenario(buy_first, [1, -1, -1], opens=[10, 10, 10])["trades"]

    assert [trade["side"] for trade in sell_first_trades if trade["date"] == "2026-01-03"] == ["SELL", "BUY"]
    assert any(
        trade["source"] == "PERIODIC_BUY" and trade["status"] == "UNFILLED"
        for trade in buy_first_trades
    )


def test_metrics_expose_current_lots_and_available_quantity():
    spec = _strategy(
        initial_capital=5000,
        cost_profile={"commission_rate": 0.001, "minimum_commission": 0.0},
    )

    scenario = _scenario(spec, [1, 1, 1], opens=[10, 10, 10])

    metrics = scenario["metrics"]
    assert metrics["current_position_quantity"] == 100
    assert metrics["current_available_quantity"] == 100
    assert len(metrics["current_position_lots"]) == 1
    assert metrics["current_position_lots"][0]["fees"] > 0


def test_unfilled_orders_have_zero_fill_and_empty_lot_ids():
    spec = _strategy(
        initial_capital=1000,
        position_sizing={"type": "fixed_quantity", "quantity": 100, "lot_size": 100},
    )
    scenario = _scenario(spec, [1, 1], opens=[20, 20])

    unfilled = next(trade for trade in scenario["trades"] if trade["status"] == "UNFILLED")
    assert unfilled["filled_quantity"] == 0
    assert unfilled["lot_ids"] == []


def test_portfolio_scope_supports_close_condition_signals():
    spec = StrategySpec.from_dict(
        {
            "strategy_id": "portfolio-close-signals",
            "version": "1.0.0",
            "name": "portfolio close signals",
            "universe": ["512890", "600000"],
            "frequency": "1d",
            "indicators": [
                {
                    "id": "rsi_2",
                    "type": "rsi",
                    "timeframe": "1d",
                    "period": 2,
                    "source": "close",
                    "method": "wilder",
                }
            ],
            "entry": {
                "mode": "count_conditions",
                "conditions": [
                    {"id": "low", "indicator": "rsi_2", "operator": "<", "value": 30}
                ],
                "amount_by_count": {"1": 1000, "2": 1000},
            },
            "exit": {"rules": []},
            "position_sizing": {
                "capital_scope": "portfolio",
                "type": "recurrent_cash",
                "lot_size": 100,
                "initial_quantity": 0,
            },
            "initial_capital": 1000,
            "execution": {
                "signal_at": "close",
                "fill_at": "close",
                "action_priority": ["EXIT", "ENTRY"],
            },
        }
    )
    bars = _bars([10, 9, 8])

    scenario = _scenario(
        spec,
        [10, 9, 8],
        symbols={"512890": bars, "600000": bars},
    )

    buys = [trade for trade in scenario["trades"] if trade["side"] == "BUY"]
    assert buys[0]["date"] == buys[0]["signal_date"] == "2026-01-03"
    assert sum(trade["quantity"] for trade in buys) == 100
    assert any(trade["status"] == "UNFILLED" for trade in buys)
