"""Daily rule observation, kept separate from AI interpretation."""

from typing import Any, Dict, Optional

from mcp_server.services.backtesting import _normalize_data, _rules_match
from mcp_server.services.indicators import build_indicator_series
from mcp_server.services.layered_sizing import (
    build_fibonacci_state,
    build_ladder_state,
    resolve_fibonacci_level,
    resolve_tactical_cash,
)
from mcp_server.services.signal_planner import build_signal_plan


class StrategyObserver:
    def observe(
        self,
        spec,
        data: Dict[str, Any],
        positions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not spec.is_valid:
            raise ValueError("策略不可观察：{}".format("；".join(spec.validation_errors)))
        normalized, warnings, _ = _normalize_data(spec, data)
        positions = positions or {}
        signals = []
        for code in spec.universe:
            bars = normalized.get(code, [])
            if not bars:
                signals.append(
                    {
                        "code": code,
                        "action": "UNDETERMINED",
                        "execution": None,
                        "status": "missing_data",
                        "evidence": {
                            "signal_date": None,
                            "source_name": spec.data_policy.get("source_name", "a-stock-data"),
                            "source_version": spec.data_policy.get(
                                "source_version", "a-stock-data:unknown"
                            ),
                            "reason": "缺少完整日线",
                        },
                    }
                )
                continue
            latest = bars[-1]
            closes = [float(bar["close"]) for bar in bars]
            index = len(bars) - 1
            position_details = _position_details(positions.get(code))
            position_quantity = position_details["total_quantity"]
            layered = bool(
                (spec.position_sizing or {}).get("core")
                or (spec.position_sizing or {}).get("drawdown_ladder")
                or (spec.position_sizing or {}).get("fibonacci_ladder")
            )
            ladder_state = None
            fibonacci_state = None
            signal_evidence = None
            base_evidence = {
                "signal_date": str(latest["date"]),
                "data_as_of": str(latest["date"]),
                "source_name": spec.data_policy.get("source_name", "a-stock-data"),
                "source_url": spec.data_policy.get("source_url"),
                "source_version": spec.data_policy.get(
                    "source_version", "a-stock-data:unknown"
                ),
                "skill_name": spec.data_policy.get("skill_name", "a-stock-data"),
                "skill_version": spec.data_policy.get("skill_version"),
                "warnings": list(warnings),
            }
            if spec.entry.get("mode") == "count_conditions":
                indicator_series = build_indicator_series(spec, bars)
                plan = build_signal_plan(
                    spec,
                    bars,
                    index,
                    int(
                        position_details["tactical_quantity"]
                        if layered
                        else position_quantity
                    ),
                    indicator_series=indicator_series,
                )
                action = plan["action"]
                if action == "SELL" and plan["sell_quantity"] <= 0:
                    action = "HOLD"
                evidence = dict(base_evidence)
                evidence.update(plan["evidence"])
                evidence["buy_cash"] = plan["buy_cash"]
                evidence["sell_quantity"] = plan["sell_quantity"]
                if layered:
                    if (spec.position_sizing or {}).get("drawdown_ladder"):
                        ladder_state = build_ladder_state(spec, bars, index)
                    elif (spec.position_sizing or {}).get("fibonacci_ladder"):
                        fibonacci_state = build_fibonacci_state(spec, bars, index)
                    evidence.update(
                        {
                            "core_quantity": position_details["core_quantity"],
                            "tactical_quantity": position_details["tactical_quantity"],
                            "ladder_state": ladder_state,
                            "fibonacci_state": fibonacci_state,
                        }
                    )
                    signal_evidence = dict(evidence)
                    signal_evidence["book"] = "tactical"
                    signal_evidence["buy_cash"] = (
                        resolve_tactical_cash(
                            plan["buy_cash"], ladder_state["ladder_amount"]
                        )
                        if ladder_state is not None and action == "BUY"
                        else plan["buy_cash"]
                    )
                    if fibonacci_state is not None and action == "BUY":
                        fibonacci_result = resolve_fibonacci_level(spec, fibonacci_state)
                        signal_evidence["buy_cash"] = resolve_tactical_cash(
                            plan["buy_cash"], fibonacci_result["ladder_amount"]
                        )
                    signal_evidence["ladder"] = (
                        {
                            "state": ladder_state,
                            "ladder_amount": ladder_state["ladder_amount"],
                        }
                        if ladder_state is not None
                        else None
                    )
                    signal_evidence["fibonacci"] = (
                        {
                            "state": fibonacci_state,
                            **resolve_fibonacci_level(spec, fibonacci_state),
                        }
                        if fibonacci_state is not None
                        else None
                    )
                    if action == "SELL":
                        signal_evidence["sell_quantity"] = plan["sell_quantity"]
                execution = (
                    "same_trading_day_close" if action in {"BUY", "SELL"} else None
                )
            else:
                entry = _rules_match(spec.entry, index, closes)
                exit_ = _rules_match(spec.exit, index, closes)
                held = position_quantity > 0
                action = "SELL" if held and exit_ else "BUY" if not held and entry else "HOLD"
                evidence = dict(base_evidence)
                evidence.update(
                    {
                        "rules": {
                            "entry": spec.entry,
                            "exit": spec.exit,
                            "entry_match": entry,
                            "exit_match": exit_,
                        },
                        "indicator_values": {"close": closes[-1]},
                        "position_quantity": position_quantity,
                    }
                )
                execution = "next_trading_day_open" if action in {"BUY", "SELL"} else None
            signal = {
                "code": code,
                "action": action,
                "execution": execution,
                "status": "ok" if not warnings else "partial",
                "evidence": evidence,
            }
            if layered:
                signal.update(
                    {
                        "core_quantity": position_details["core_quantity"],
                        "tactical_quantity": position_details["tactical_quantity"],
                        "ladder_state": ladder_state,
                        "fibonacci_state": fibonacci_state,
                        "signal_evidence": signal_evidence or evidence,
                    }
                )
            signals.append(signal)
        return {
            "strategy_id": spec.strategy_id,
            "strategy_version": spec.version,
            "signals": signals,
            "ai_observation": {
                "status": "not_generated",
                "reason": "AI 观察必须由对话 Skill 基于上述规则证据单独生成",
            },
            "warnings": sorted(set(warnings)),
        }


def _position_quantity(value):
    return _position_details(value)["total_quantity"]


def _position_details(value):
    if isinstance(value, dict) and ("core" in value or "tactical" in value):
        core_quantity = _book_quantity(value.get("core"))
        tactical_quantity = _book_quantity(value.get("tactical"))
        return {
            "core_quantity": core_quantity,
            "tactical_quantity": tactical_quantity,
            "total_quantity": core_quantity + tactical_quantity,
        }
    quantity = value.get("quantity", 0) if isinstance(value, dict) else value
    try:
        total = float(quantity or 0)
    except (TypeError, ValueError):
        total = 0.0
    return {"core_quantity": 0.0, "tactical_quantity": total, "total_quantity": total}


def _book_quantity(value):
    if not isinstance(value, dict):
        return 0.0
    quantity = value.get("quantity")
    if quantity is None:
        quantity = sum(
            float(lot.get("quantity", 0) or 0)
            for lot in value.get("lots", [])
            if isinstance(lot, dict)
        )
    try:
        return float(quantity or 0)
    except (TypeError, ValueError):
        return 0.0
