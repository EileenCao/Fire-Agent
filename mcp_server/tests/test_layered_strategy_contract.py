from copy import deepcopy

from mcp_server.domain.strategy import StrategySpec


def _layered_payload():
    return {
        "strategy_id": "etf-512890-layered",
        "version": "1.0.0",
        "name": "512890 layered",
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
            "core": {
                "ratio": 0.5,
                "trigger": "first_entry_signal",
                "hold": True,
            },
            "drawdown_ladder": {
                "anchor_window": 120,
                "thresholds": [0.10, 0.20, 0.30],
                "amounts": [500, 1000, 1500],
                "annual_period": 250,
                "annual_boost_threshold": 0.0,
                "annual_deep_threshold": 0.05,
                "max_level": 3,
                "combine": "max",
                "requires_rsi_entry": True,
                "reset": "new_anchor_high",
            },
        },
        "initial_capital": 50000,
        "cost_profile": {"template": "theoretical", "version": "1.0.0"},
        "execution": {"signal_at": "close", "fill_at": "close"},
    }


def test_layered_position_sizing_round_trips_core_and_ladder_contract():
    spec = StrategySpec.from_dict(_layered_payload())

    assert spec.is_valid
    sizing = spec.to_dict()["position_sizing"]
    assert sizing["core"]["ratio"] == 0.5
    assert sizing["drawdown_ladder"]["thresholds"] == [0.10, 0.20, 0.30]
    assert sizing["drawdown_ladder"]["combine"] == "max"


def test_layered_position_sizing_rejects_invalid_core_ratio():
    payload = _layered_payload()
    payload["position_sizing"]["core"]["ratio"] = 1.1

    spec = StrategySpec.from_dict(payload)

    assert not spec.is_valid
    assert any("core" in error and "ratio" in error for error in spec.validation_errors)


def test_layered_position_sizing_rejects_invalid_ladder_shape():
    payload = deepcopy(_layered_payload())
    payload["position_sizing"]["drawdown_ladder"]["thresholds"] = [0.20, 0.10]
    payload["position_sizing"]["drawdown_ladder"]["amounts"] = [500]
    payload["position_sizing"]["drawdown_ladder"]["combine"] = "sum"

    spec = StrategySpec.from_dict(payload)

    assert not spec.is_valid
    assert any("drawdown_ladder" in error for error in spec.validation_errors)
