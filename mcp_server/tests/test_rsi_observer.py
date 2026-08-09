from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.observer import StrategyObserver


def _spec():
    return StrategySpec.from_dict(
        {
            "strategy_id": "observer-rsi",
            "version": "1.0.0",
            "name": "observer RSI",
            "universe": ["512890"],
            "frequency": "1d",
            "indicators": [
                {"id": "rsi_a", "type": "rsi", "timeframe": "1d", "period": 2, "source": "close", "method": "wilder"},
                {"id": "rsi_b", "type": "rsi", "timeframe": "1d", "period": 2, "source": "close", "method": "wilder"},
            ],
            "entry": {
                "mode": "count_conditions",
                "conditions": [
                    {"id": "a_low", "indicator": "rsi_a", "operator": "<", "value": 30},
                    {"id": "b_low", "indicator": "rsi_b", "operator": "<", "value": 30},
                ],
                "amount_by_count": {"1": 500, "2": 1000},
            },
            "exit": {
                "mode": "count_conditions",
                "conditions": [{"id": "a_high", "indicator": "rsi_a", "operator": ">", "value": 80}],
                "fraction_by_count": {"1": 0.2, "2": 1 / 3, "3": 1.0},
            },
            "position_sizing": {"type": "recurrent_cash", "lot_size": 100, "initial_quantity": 0},
            "initial_capital": 50000,
            "execution": {"signal_at": "close", "fill_at": "close", "action_priority": ["EXIT", "ENTRY"]},
        }
    )


def _bars():
    return {
        "512890": [
            {"date": "2026-01-01", "open": 4, "high": 4, "low": 4, "close": 4},
            {"date": "2026-01-02", "open": 3, "high": 3, "low": 3, "close": 3},
            {"date": "2026-01-03", "open": 2, "high": 2, "low": 2, "close": 2},
        ]
    }


def test_rsi_observer_returns_close_signal_evidence_for_current_position():
    result = StrategyObserver().observe(
        _spec(), _bars(), positions={"512890": {"quantity": 100}}
    )

    signal = result["signals"][0]
    assert signal["action"] == "BUY"
    assert signal["execution"] == "same_trading_day_close"
    assert signal["evidence"]["entry_count"] == 2
    assert signal["evidence"]["position_quantity"] == 100
