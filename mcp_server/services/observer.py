"""Daily rule observation, kept separate from AI interpretation."""

from typing import Any, Dict, Optional

from mcp_server.services.backtesting import _normalize_data, _rules_match
from mcp_server.services.indicators import build_indicator_series
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
            position_quantity = _position_quantity(positions.get(code))
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
                    int(position_quantity),
                    indicator_series=indicator_series,
                )
                action = plan["action"]
                if action == "SELL" and plan["sell_quantity"] <= 0:
                    action = "HOLD"
                evidence = dict(base_evidence)
                evidence.update(plan["evidence"])
                evidence["buy_cash"] = plan["buy_cash"]
                evidence["sell_quantity"] = plan["sell_quantity"]
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
            signals.append(
                {
                    "code": code,
                    "action": action,
                    "execution": execution,
                    "status": "ok" if not warnings else "partial",
                    "evidence": evidence,
                }
            )
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
    if isinstance(value, dict):
        value = value.get("quantity", 0)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
