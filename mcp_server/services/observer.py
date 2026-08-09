"""Daily rule observation, kept separate from AI interpretation."""

from typing import Any, Dict, Optional

from mcp_server.services.backtesting import _normalize_data, _rules_match


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
            entry = _rules_match(spec.entry, index, closes)
            exit_ = _rules_match(spec.exit, index, closes)
            held = _position_quantity(positions.get(code)) > 0
            action = "SELL" if held and exit_ else "BUY" if not held and entry else "HOLD"
            evidence = {
                "signal_date": str(latest["date"]),
                "data_as_of": str(latest["date"]),
                "source_name": spec.data_policy.get("source_name", "a-stock-data"),
                "source_url": spec.data_policy.get("source_url"),
                "source_version": spec.data_policy.get(
                    "source_version", "a-stock-data:unknown"
                ),
                "skill_name": spec.data_policy.get("skill_name", "a-stock-data"),
                "skill_version": spec.data_policy.get("skill_version"),
                "rules": {
                    "entry": spec.entry,
                    "exit": spec.exit,
                    "entry_match": entry,
                    "exit_match": exit_,
                },
                "indicator_values": {"close": closes[-1]},
                "position_quantity": _position_quantity(positions.get(code)),
                "warnings": list(warnings),
            }
            signals.append(
                {
                    "code": code,
                    "action": action,
                    "execution": "next_trading_day_open" if action in {"BUY", "SELL"} else None,
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
