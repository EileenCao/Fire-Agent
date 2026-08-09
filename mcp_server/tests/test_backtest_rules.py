from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.backtesting import BacktestEngine


def _strategy(**overrides):
    payload = {
        "strategy_id": "rule-checks",
        "version": "1.0.0",
        "name": "交易约束测试",
        "universe": ["512890"],
        "frequency": "1d",
        "entry": {"rules": [{"type": "state", "left": "close", "right": 1}]},
        "exit": {"rules": [{"type": "state", "left": "close", "right": 11, "operator": "<"}]},
        "position_sizing": {"type": "all_in", "lot_size": 100},
        "initial_capital": 10000,
        "cost_profile": {
            "template": "realistic",
            "version": "1.0.0",
            "commission_rate": 0.001,
            "stamp_duty_rate": 0.001,
            "slippage_rate": 0.0,
        },
    }
    payload.update(overrides)
    return StrategySpec.from_dict(payload)


def test_t1_lot_size_and_adjusted_prices_are_recorded():
    spec = _strategy(
        entry={"rules": [{"type": "state", "left": "close", "right": 1}]},
        exit={"rules": []},
    )
    bars = [
        {"date": "2026-01-01", "open": 2, "high": 2, "low": 2, "close": 2},
        {
            "date": "2026-01-02",
            "open": 10,
            "high": 10,
            "low": 5,
            "close": 10,
            "adj_factor": 2,
            "corporate_action": "现金分红",
            "cash_dividend": 0.1,
        },
        {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10},
    ]

    result = BacktestEngine().run(spec, {"512890": bars})
    scenario = result["scenarios"]["default"]
    buy = next(trade for trade in scenario["trades"] if trade["side"] == "BUY")

    assert buy["quantity"] == 900
    assert not any(
        trade["side"] == "SELL" and trade["date"] == "2026-01-02"
        for trade in scenario["trades"]
    )
    assert result["provenance"]["corporate_actions"] == [
        {"code": "512890", "date": "2026-01-02", "action": "现金分红"}
    ]
    assert result["provenance"]["cost_profile"]["version"] == "1.0.0"


def test_limit_up_and_suspension_create_explicit_unfilled_order():
    spec = _strategy(exit={"rules": []})
    bars = [
        {"date": "2026-01-01", "open": 2, "high": 2, "low": 2, "close": 2},
        {
            "date": "2026-01-02",
            "open": 10,
            "high": 10,
            "low": 10,
            "close": 10,
            "limit_up": 10,
        },
        {
            "date": "2026-01-03",
            "open": 10,
            "high": 10,
            "low": 10,
            "close": 10,
            "suspended": True,
        },
    ]

    result = BacktestEngine().run(spec, {"512890": bars})
    scenario = result["scenarios"]["default"]
    unfilled = [trade for trade in scenario["trades"] if trade.get("status") == "UNFILLED"]

    assert unfilled
    assert any("涨跌停" in warning or "停牌" in warning for warning in scenario["warnings"])


def test_missing_bars_are_reported_instead_of_raising_key_error():
    spec = _strategy()
    bars = [
        {"date": "2026-01-01", "open": 2, "high": 2, "close": 2},
        {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
    ]

    result = BacktestEngine().run(spec, {"512890": bars})

    assert result["warnings"]
    assert any("缺失" in warning for warning in result["warnings"])
