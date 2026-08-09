from mcp_server.domain.strategy import StrategySpec


def _rsi_payload():
    return {
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
                {
                    "id": "daily_rsi_6_low",
                    "indicator": "daily_rsi_6",
                    "operator": "<",
                    "value": 30,
                },
                {
                    "id": "weekly_rsi_14_low",
                    "indicator": "weekly_rsi_14",
                    "operator": "<",
                    "value": 47,
                },
            ],
            "amount_by_count": {"1": 500, "2": 1000},
        },
        "exit": {
            "mode": "count_conditions",
            "conditions": [
                {
                    "id": "daily_rsi_6_high",
                    "indicator": "daily_rsi_6",
                    "operator": ">",
                    "value": 80,
                },
                {
                    "id": "daily_rsi_12_high",
                    "indicator": "daily_rsi_12",
                    "operator": ">",
                    "value": 70,
                },
                {
                    "id": "weekly_rsi_14_high",
                    "indicator": "weekly_rsi_14",
                    "operator": ">",
                    "value": 63,
                },
            ],
            "fraction_by_count": {"1": 0.2, "2": 1 / 3, "3": 1.0},
        },
        "position_sizing": {
            "type": "recurrent_cash",
            "lot_size": 100,
            "initial_quantity": 0,
        },
        "initial_capital": 50000,
        "cost_profile": {"template": "theoretical", "version": "1.0.0"},
        "execution": {
            "signal_at": "close",
            "fill_at": "close",
            "action_priority": ["EXIT", "ENTRY"],
        },
    }


def test_rsi_strategy_round_trips_extended_fields():
    spec = StrategySpec.from_dict(_rsi_payload())

    assert spec.is_valid
    assert spec.to_dict()["indicators"][2]["completed_only"] is True
    assert spec.to_dict()["execution"]["fill_at"] == "close"


def test_legacy_strategy_without_execution_policy_stays_valid():
    spec = StrategySpec.from_dict(
        {
            "strategy_id": "legacy",
            "version": "1.0.0",
            "name": "legacy",
            "universe": ["600000"],
            "frequency": "1d",
            "entry": {"rules": [{"type": "state", "left": "close", "right": 1}]},
            "exit": {"rules": []},
            "position_sizing": {"type": "all_in"},
        }
    )

    assert spec.is_valid
    assert spec.to_dict()["execution"] == {}


def test_rsi_strategy_rejects_invalid_condition_counts_and_execution():
    payload = _rsi_payload()
    payload["entry"]["amount_by_count"] = {"1": 500}
    payload["execution"]["fill_at"] = "next_open"

    spec = StrategySpec.from_dict(payload)

    assert not spec.is_valid
    assert any(
        "amount_by_count" in error or "fill_at" in error
        for error in spec.validation_errors
    )
