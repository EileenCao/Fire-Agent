"""Operational strategy signals using a clearly labelled morning approximation."""

from datetime import date
from typing import Any, Dict, Iterable, List

from mcp_server.services.backtesting import BacktestEngine
from mcp_server.services.indicators import build_indicator_series
from mcp_server.services.layered_sizing import (
    build_ladder_state,
    resolve_ladder_level,
    resolve_tactical_cash,
)
from mcp_server.services.signal_planner import build_signal_plan


class MorningStrategySignalEvaluator:
    """Replay prior bars, then evaluate one synthetic 11:30 signal bar."""

    mode = "morning_close_approximation"

    def evaluate(
        self,
        spec,
        bars: Iterable[Dict[str, Any]],
        report_date: date,
        morning_price: float,
        data_as_of: str,
    ) -> Dict[str, Any]:
        if not spec.is_valid:
            return {"status": "invalid_strategy", "error": "; ".join(spec.validation_errors)}
        history = [dict(bar) for bar in bars]
        if not history:
            return {"status": "missing_data", "error": "历史日线为空"}

        code = spec.universe[0]
        previous = BacktestEngine().run(spec, {code: history})["scenarios"]["default"]
        current = dict(history[-1])
        current.update(
            {
                "date": report_date.isoformat(),
                "open": float(morning_price),
                "high": float(morning_price),
                "low": float(morning_price),
                "close": float(morning_price),
                "synthetic_morning_close": True,
            }
        )
        bars_with_morning = history + [current]
        series = build_indicator_series(spec, bars_with_morning)
        position = (previous.get("positions") or {}).get(code) or {}
        tactical = position.get("tactical") or {}
        profitable_quantity = _profitable_quantity(
            tactical.get("lots", []), float(morning_price), report_date.isoformat()
        )
        plan = build_signal_plan(
            spec,
            bars_with_morning,
            len(bars_with_morning) - 1,
            profitable_quantity,
            indicator_series=series,
        )
        ladder = build_ladder_state(spec, bars_with_morning, len(bars_with_morning) - 1)
        triggered = _triggered_ladder_levels(previous.get("trades", []))
        ladder_result = resolve_ladder_level(spec, ladder, triggered)
        buy_cash = resolve_tactical_cash(plan["buy_cash"], ladder_result["ladder_amount"])
        evidence = dict(plan["evidence"])
        evidence.update(
            {
                "data_as_of": report_date.isoformat(),
                "morning_price": float(morning_price),
                "signal_mode": self.mode,
                "weekly_rsi_mode": "synthetic_current_week",
                "profitable_tactical_quantity": profitable_quantity,
                "ladder": {
                    "state": ladder,
                    "new_levels": ladder_result["new_levels"],
                    "triggered_levels": ladder_result["triggered_levels"],
                    "ladder_amount": ladder_result["ladder_amount"],
                },
            }
        )
        state = dict(position)
        state["market_value"] = round(
            float(position.get("total_quantity", 0)) * float(morning_price), 8
        )
        return {
            "status": "ok",
            "code": code,
            "mode": self.mode,
            "strategy_id": spec.strategy_id,
            "strategy_version": spec.version,
            "data_as_of": data_as_of,
            "action": plan["action"],
            "signal": {
                **plan,
                "buy_cash": buy_cash,
                "evidence": evidence,
            },
            "state": state,
        }


def _profitable_quantity(lots: Iterable[dict], price: float, day: str) -> int:
    return sum(
        int(lot.get("quantity", 0) or 0)
        for lot in lots
        if lot.get("available_date", "9999-12-31") <= day
        and float(lot.get("price", 0) or 0) < price
    )


def _triggered_ladder_levels(trades: Iterable[dict]) -> set:
    levels = set()
    for trade in trades:
        ladder = (trade.get("signal_evidence") or {}).get("ladder") or {}
        levels.update(int(value) for value in ladder.get("triggered_levels", []))
    return levels
