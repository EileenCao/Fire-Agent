"""Deterministic daily-bar engine with explicit A-share execution constraints."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from math import floor
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mcp_server.domain.identifiers import normalize_ticker
from mcp_server.services.indicators import build_indicator_series
from mcp_server.services.layered_sizing import (
    build_ladder_state,
    build_fibonacci_state,
    resolve_ladder_level,
    resolve_fibonacci_level,
    resolve_recovery_levels,
    resolve_tactical_cash,
)
from mcp_server.services.performance import calculate_performance_metrics
from mcp_server.services.signal_planner import build_signal_plan


REQUIRED_BAR_FIELDS = ("date", "open", "high", "low", "close")
DEFAULT_LOT_SIZE = 100


@dataclass
class PositionLot:
    """One buy batch tracked separately so A-share T+1 can be enforced."""

    lot_id: str
    code: str
    buy_date: str
    available_date: str
    quantity: int
    price: float
    cost: float
    source: str
    fees: float = 0.0


@dataclass
class PositionBook:
    """FIFO position book with explicit available quantities."""

    code: str
    lots: List[PositionLot] = field(default_factory=list)

    def total_quantity(self) -> int:
        return sum(max(0, lot.quantity) for lot in self.lots)

    def available_quantity(self, day: str) -> int:
        return sum(
            max(0, lot.quantity)
            for lot in self.lots
            if lot.quantity > 0 and lot.available_date <= day
        )

    def available_profitable_quantity(self, price: float, day: str) -> int:
        """Return T+1-available lots whose buy price is below current price."""

        return sum(
            max(0, lot.quantity)
            for lot in self.lots
            if lot.quantity > 0
            and lot.available_date <= day
            and float(price) > lot.price
        )

    def quantity_before(self, day: str) -> int:
        return sum(
            max(0, lot.quantity)
            for lot in self.lots
            if lot.quantity > 0 and lot.buy_date < day
        )

    def average_cost(self) -> Optional[float]:
        quantity = self.total_quantity()
        if quantity <= 0:
            return None
        return sum(lot.cost for lot in self.lots if lot.quantity > 0) / quantity

    def market_value(self, price: float) -> float:
        return self.total_quantity() * float(price)

    def add(self, lot: PositionLot) -> None:
        self.lots.append(lot)

    def sell(self, quantity: int, day: str) -> Tuple[int, List[Dict[str, Any]]]:
        remaining = max(0, int(quantity))
        allocations: List[Dict[str, Any]] = []
        for lot in self.lots:
            if remaining <= 0:
                break
            if lot.quantity <= 0 or lot.available_date > day:
                continue
            sold = min(lot.quantity, remaining)
            unit_cost = lot.cost / lot.quantity
            allocated_cost = unit_cost * sold
            allocated_fees = (lot.fees / lot.quantity) * sold
            lot.quantity -= sold
            lot.cost -= allocated_cost
            lot.fees -= allocated_fees
            remaining -= sold
            allocations.append(
                {
                    "lot_id": lot.lot_id,
                    "buy_date": lot.buy_date,
                    "quantity": sold,
                    "allocated_cost": allocated_cost,
                    "allocated_fees": allocated_fees,
                    "holding_days": max(
                        0, (date.fromisoformat(day) - date.fromisoformat(lot.buy_date)).days
                    ),
                }
            )
        self.lots = [lot for lot in self.lots if lot.quantity > 0]
        return quantity - remaining, allocations

    def sell_profitable(
        self, quantity: int, price: float, day: str
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Sell only available lots with a buy price below current price."""

        remaining = max(0, int(quantity))
        allocations: List[Dict[str, Any]] = []
        for lot in self.lots:
            if remaining <= 0:
                break
            if (
                lot.quantity <= 0
                or lot.available_date > day
                or float(price) <= lot.price
            ):
                continue
            sold = min(lot.quantity, remaining)
            unit_cost = lot.cost / lot.quantity
            allocated_cost = unit_cost * sold
            allocated_fees = (lot.fees / lot.quantity) * sold
            lot.quantity -= sold
            lot.cost -= allocated_cost
            lot.fees -= allocated_fees
            remaining -= sold
            allocations.append(
                {
                    "lot_id": lot.lot_id,
                    "buy_date": lot.buy_date,
                    "quantity": sold,
                    "allocated_cost": allocated_cost,
                    "allocated_fees": allocated_fees,
                    "holding_days": max(
                        0,
                        (
                            date.fromisoformat(day)
                            - date.fromisoformat(lot.buy_date)
                        ).days,
                    ),
                }
            )
        self.lots = [lot for lot in self.lots if lot.quantity > 0]
        return quantity - remaining, allocations


@dataclass(frozen=True)
class PendingOrder:
    code: str
    side: str
    source: str
    signal_date: str
    execute_date: str
    reason: str
    config: Dict[str, Any] = field(default_factory=dict)
    requested_quantity: Optional[int] = None
    amount: Optional[float] = None
    funding: str = "existing_cash"
    intraday_price: Optional[float] = None
    priority: Optional[int] = None
    evidence: Optional[Dict[str, Any]] = None


class BacktestEngine:
    """Run a long-only strategy using close signals and next-open execution.

    The engine accepts normalized JSON-like bars.  A data adapter may provide
    ``adj_open``/``adj_high``/``adj_low``/``adj_close`` or ``raw_*`` plus an
    ``adj_factor``; the values used by the engine are then explicitly recorded
    as adjusted prices in the result provenance.
    """

    def run(self, spec, data: Dict[str, Iterable[Dict[str, Any]]]) -> Dict[str, Any]:
        if not spec.is_valid:
            raise ValueError("策略不可运行：{}".format("；".join(spec.validation_errors)))
        normalized, data_warnings, actions = _normalize_data(spec, data)
        if not normalized:
            raise ValueError("回测数据为空或没有可用的完整日线")

        scenarios = (
            ["stop_first", "take_first"]
            if spec.stop_loss_pct is not None and spec.take_profit_pct is not None
            else ["default"]
        )
        result = {
            "strategy_id": spec.strategy_id,
            "strategy_version": spec.version,
            "provenance": _provenance(spec, normalized, actions),
            "warnings": sorted(set(data_warnings)),
            "scenarios": {},
        }
        for scenario in scenarios:
            result["scenarios"][scenario] = self._run_scenario(
                spec, normalized, scenario
            )
        result["warnings"] = sorted(
            set(result["warnings"])
            | {
                warning
                for scenario in result["scenarios"].values()
                for warning in scenario.get("warnings", [])
            }
        )
        result["validation"] = self._validation_summary(spec, normalized, scenarios)
        return result

    def _run_scenario(self, spec, data, scenario: str) -> Dict[str, Any]:
        sizing = spec.position_sizing or {}
        if sizing.get("capital_scope", "per_symbol") == "portfolio":
            return self._run_portfolio(spec, data, scenario)

        allocation = spec.initial_capital / max(1, len(data))
        total_equity: Dict[str, float] = {}
        total_cash: Dict[str, float] = {}
        total_market_value: Dict[str, float] = {}
        all_trades: List[Dict[str, Any]] = []
        all_warnings: List[str] = []
        cash_flows: List[Dict[str, Any]] = []
        skipped_sell_signals: List[Dict[str, Any]] = []
        positions: Dict[str, Any] = {}
        for code, bars in data.items():
            outcome = self._run_symbol(spec, code, bars, allocation, scenario)
            all_trades.extend(outcome["trades"])
            all_warnings.extend(outcome["warnings"])
            cash_flows.extend(outcome["cash_flows"])
            skipped_sell_signals.extend(outcome.get("skipped_sell_signals", []))
            positions[code] = outcome["positions"]
            for day, equity in outcome["equity_curve"].items():
                total_equity[day] = total_equity.get(day, 0.0) + equity
            for day, cash in outcome["cash_curve"].items():
                total_cash[day] = total_cash.get(day, 0.0) + cash
            for day, market_value in outcome["market_value_curve"].items():
                total_market_value[day] = total_market_value.get(day, 0.0) + market_value
        if not total_equity:
            total_equity = {"": spec.initial_capital}
        values = [total_equity[key] for key in sorted(total_equity)]
        metrics = _metrics(
            spec.initial_capital,
            total_equity,
            values,
            all_trades,
            cash_flows,
            positions,
            risk_free_rate_annual=spec.risk_free_rate_annual,
            cash_curve=total_cash,
            market_value_curve=total_market_value,
        )
        if skipped_sell_signals:
            metrics["skipped_sell_signal_count"] = len(skipped_sell_signals)
        return {
            "scenario": scenario,
            "equity_curve": total_equity,
            "cash_curve": total_cash,
            "market_value_curve": total_market_value,
            "exposure_curve": {
                day: (total_market_value.get(day, 0.0) / equity if equity else 0.0)
                for day, equity in total_equity.items()
            },
            "trades": all_trades,
            "cash_flows": cash_flows,
            "positions": positions,
            "metrics": metrics,
            "warnings": sorted(set(all_warnings)),
            "skipped_sell_signals": skipped_sell_signals,
        }

    def _run_symbol(self, spec, code, raw_bars, allocation, scenario):
        if _uses_layered_close_execution(spec):
            return self._run_symbol_layered_close_execution(
                spec, code, raw_bars, allocation, scenario
            )
        if _uses_close_execution(spec):
            return self._run_symbol_close_execution(
                spec, code, raw_bars, allocation, scenario
            )

        bars = list(raw_bars)
        dates = [str(bar["date"]) for bar in bars]
        closes = [float(bar["close"]) for bar in bars]
        lot_size = _lot_size(spec)
        book = PositionBook(code)
        cash = float(allocation)
        pending: List[PendingOrder] = []
        trades: List[Dict[str, Any]] = []
        warnings: List[str] = []
        cash_flows: List[Dict[str, Any]] = []
        equity_curve: Dict[str, float] = {}
        cash_curve: Dict[str, float] = {}
        market_value_curve: Dict[str, float] = {}
        periodic_events = _periodic_events(spec, bars)
        order_number = 0

        for index, bar in enumerate(bars):
            day = str(bar["date"])
            next_day = _next_date(dates, day)
            suspended = bool(bar.get("suspended") or bar.get("is_suspended"))

            for event in periodic_events.get(day, []):
                if book.total_quantity() > 0:
                    pending.append(
                        _periodic_order(code, event, day)
                    )

            due = [order for order in pending if order.execute_date == day]
            pending = [order for order in pending if order.execute_date != day]
            due.sort(key=lambda order: _order_sort_key(spec, order, 0))
            for order in due:
                order_number += 1
                trade, cash, flow, order_warnings = self._execute_order(
                    spec,
                    code,
                    bar,
                    order,
                    book,
                    cash,
                    allocation,
                    lot_size,
                    next_day,
                    order_number,
                )
                trades.append(trade)
                if flow:
                    cash_flows.append(flow)
                warnings.extend(order_warnings)

            if book.quantity_before(day) > 0:
                dividend = _as_float(bar.get("cash_dividend"))
                if dividend and dividend > 0:
                    amount = book.quantity_before(day) * dividend
                    cash += amount
                    warnings.append(
                        "{} {} 分红单独入账：{:.8f}".format(code, day, amount)
                    )

            if book.total_quantity() > 0 and not suspended:
                average_cost = book.average_cost()
                stop_take = _stop_take_trigger(spec, bar, average_cost, scenario)
                if stop_take is not None:
                    reason, exit_price = stop_take
                    order_number += 1
                    trade, cash, flow, order_warnings = self._execute_order(
                        spec,
                        code,
                        bar,
                        PendingOrder(
                            code=code,
                            side="SELL",
                            source=reason,
                            signal_date=day,
                            execute_date=day,
                            reason=reason,
                            requested_quantity=book.available_quantity(day),
                            intraday_price=exit_price,
                        ),
                        book,
                        cash,
                        allocation,
                        lot_size,
                        next_day,
                        order_number,
                    )
                    trades.append(trade)
                    if flow:
                        cash_flows.append(flow)
                    warnings.extend(order_warnings)

            if suspended:
                warnings.append("{} {} 停牌，无法产生或执行交易".format(code, day))
            else:
                entry_match = _rules_match(spec.entry, index, closes)
                exit_match = _rules_match(spec.exit, index, closes)
                if book.total_quantity() == 0 and entry_match:
                    _queue_next_order(
                        pending,
                        PendingOrder(
                            code=code,
                            side="BUY",
                            source="SIGNAL_BUY",
                            signal_date=day,
                            execute_date=next_day or day,
                            reason="ENTRY_RULE",
                            config=dict(spec.position_sizing or {}),
                        ),
                    )
                elif book.total_quantity() > 0:
                    if exit_match:
                        _queue_next_order(
                            pending,
                            PendingOrder(
                                code=code,
                                side="SELL",
                                source="SELL",
                                signal_date=day,
                                execute_date=next_day or day,
                                reason="EXIT_RULE",
                            ),
                        )
                    signal_add = _signal_add_config(spec)
                    if entry_match and signal_add:
                        _queue_next_order(
                            pending,
                            PendingOrder(
                                code=code,
                                side="BUY",
                                source="SIGNAL_BUY",
                                signal_date=day,
                                execute_date=next_day or day,
                                reason="ENTRY_RULE_ADD",
                                config=signal_add,
                            ),
                        )

            equity_curve[day] = cash + book.market_value(float(bar["close"]))
            cash_curve[day] = cash
            market_value_curve[day] = book.market_value(float(bar["close"]))

        if book.total_quantity() > 0:
            warnings.append("{} 数据结束时仍有未平仓头寸，按最后收盘价估值".format(code))
        return {
            "trades": trades,
            "equity_curve": equity_curve,
            "cash_curve": cash_curve,
            "market_value_curve": market_value_curve,
            "exposure_curve": {
                day: (market_value_curve[day] / equity if equity else 0.0)
                for day, equity in equity_curve.items()
            },
            "warnings": warnings,
            "cash_flows": cash_flows,
            "positions": _position_snapshot(book),
        }

    def _run_symbol_close_execution(self, spec, code, raw_bars, allocation, scenario):
        """Run an explicitly configured condition-count strategy at the close."""

        bars = list(raw_bars)
        dates = [str(bar["date"]) for bar in bars]
        lot_size = _lot_size(spec)
        indicator_series = build_indicator_series(spec, bars)
        book = PositionBook(code)
        cash = float(allocation)
        trades: List[Dict[str, Any]] = []
        warnings: List[str] = []
        equity_curve: Dict[str, float] = {}
        cash_curve: Dict[str, float] = {}
        market_value_curve: Dict[str, float] = {}
        cash_flows: List[Dict[str, Any]] = []
        order_number = 0

        for index, bar in enumerate(bars):
            day = str(bar["date"])
            next_day = _next_date(dates, day)
            suspended = bool(bar.get("suspended") or bar.get("is_suspended"))

            if book.total_quantity() > 0 and not suspended:
                stop_take = _stop_take_trigger(spec, bar, book.average_cost(), scenario)
                if stop_take is not None:
                    reason, exit_price = stop_take
                    order_number += 1
                    trade, cash, flow, order_warnings = self._execute_order(
                        spec,
                        code,
                        bar,
                        PendingOrder(
                            code=code,
                            side="SELL",
                            source=reason,
                            signal_date=day,
                            execute_date=day,
                            reason=reason,
                            requested_quantity=book.available_quantity(day),
                            intraday_price=exit_price,
                            config={"cost_basis": "weighted_average"},
                        ),
                        book,
                        cash,
                        allocation,
                        lot_size,
                        next_day,
                        order_number,
                    )
                    trades.append(trade)
                    warnings.extend(order_warnings)
                    if flow:
                        cash_flows.append(flow)

            if suspended:
                warnings.append("{} {} 停牌，无法产生或执行交易".format(code, day))
            else:
                plan = build_signal_plan(
                    spec,
                    bars,
                    index,
                    book.total_quantity(),
                    indicator_series=indicator_series,
                )
                if plan["action"] in {"BUY", "SELL"}:
                    order_number += 1
                    config = {
                        "type": "recurrent_cash",
                        "amount": plan["buy_cash"],
                        "lot_size": lot_size,
                        "signal_evidence": plan["evidence"],
                        "cost_basis": "weighted_average",
                    }
                    order = PendingOrder(
                        code=code,
                        side=plan["action"],
                        source="SIGNAL_BUY" if plan["action"] == "BUY" else "SELL",
                        signal_date=day,
                        execute_date=day,
                        reason="ENTRY_RULE" if plan["action"] == "BUY" else "EXIT_RULE",
                        config=config,
                        requested_quantity=(
                            plan["sell_quantity"] if plan["action"] == "SELL" else None
                        ),
                        intraday_price=float(bar["close"]),
                    )
                    trade, cash, flow, order_warnings = self._execute_order(
                        spec,
                        code,
                        bar,
                        order,
                        book,
                        cash,
                        allocation,
                        lot_size,
                        next_day,
                        order_number,
                    )
                    trades.append(trade)
                    warnings.extend(order_warnings)
                    if flow:
                        cash_flows.append(flow)

            equity_curve[day] = cash + book.market_value(float(bar["close"]))
            cash_curve[day] = cash
            market_value_curve[day] = book.market_value(float(bar["close"]))

        if book.total_quantity() > 0:
            warnings.append("{} 数据结束时仍有未平仓头寸，按最后收盘价估值".format(code))
        return {
            "trades": trades,
            "equity_curve": equity_curve,
            "cash_curve": cash_curve,
            "market_value_curve": market_value_curve,
            "exposure_curve": {
                day: (market_value_curve[day] / equity if equity else 0.0)
                for day, equity in equity_curve.items()
            },
            "warnings": warnings,
            "cash_flows": cash_flows,
            "positions": _position_snapshot(book),
        }

    def _run_symbol_layered_close_execution(
        self, spec, code, raw_bars, allocation, scenario
    ):
        """Run a same-close strategy with independent core and tactical books."""

        bars = list(raw_bars)
        dates = [str(bar["date"]) for bar in bars]
        lot_size = _lot_size(spec)
        indicator_series = build_indicator_series(spec, bars)
        core_book = PositionBook(code)
        tactical_book = PositionBook(code)
        cash = float(allocation)
        core_initialized = False
        triggered_levels = set()
        recovery_tracker: Dict[str, Any] = {}
        layered_sizing = spec.position_sizing or {}
        ladder_enabled = bool(layered_sizing.get("drawdown_ladder"))
        fibonacci_enabled = bool(layered_sizing.get("fibonacci_ladder"))
        exit_mode = str(layered_sizing.get("exit_mode", "rsi"))
        sell_basis = str(layered_sizing.get("sell_basis", "all_tactical"))
        core_config = layered_sizing.get("core") or {}
        core_ratio = float(core_config.get("ratio", 0.0))
        trades: List[Dict[str, Any]] = []
        warnings: List[str] = []
        equity_curve: Dict[str, float] = {}
        cash_curve: Dict[str, float] = {}
        market_value_curve: Dict[str, float] = {}
        cash_flows: List[Dict[str, Any]] = []
        skipped_sell_signals: List[Dict[str, Any]] = []
        order_number = 0

        for index, bar in enumerate(bars):
            day = str(bar["date"])
            next_day = _next_date(dates, day)
            suspended = bool(bar.get("suspended") or bar.get("is_suspended"))
            ladder_state = None
            ladder_result = {"ladder_amount": 0.0, "new_levels": []}
            fibonacci_state = None
            fibonacci_result = {"ladder_amount": 0.0, "new_levels": []}
            if ladder_enabled:
                ladder_state = build_ladder_state(spec, bars, index)
                ladder_result = resolve_ladder_level(
                    spec, ladder_state, triggered_levels, consume=False
                )
                triggered_levels = set(ladder_result["triggered_levels"])
            elif fibonacci_enabled:
                fibonacci_state = build_fibonacci_state(spec, bars, index)
                fibonacci_result = resolve_fibonacci_level(spec, fibonacci_state)

            if suspended:
                warnings.append("{} {} 停牌，无法产生或执行交易".format(code, day))
            else:
                if exit_mode == "recovery" and ladder_state is not None:
                    recovery_result = resolve_recovery_levels(
                        ladder_state, recovery_tracker
                    )
                    recovery_tracker = recovery_result["tracker"]
                    ladder = layered_sizing.get("drawdown_ladder") or {}
                    amounts = [float(value) for value in ladder.get("amounts", [])]
                    for level in recovery_result["sell_levels"]:
                        amount = amounts[level - 1] if level <= len(amounts) else 0.0
                        requested_quantity = _recovery_sell_quantity(
                            amount, float(bar["close"]), lot_size
                        )
                        signal_evidence = {
                            "signal_date": day,
                            "data_as_of": day,
                            "book": "tactical",
                            "recovery": {
                                "level": level,
                                "cash_amount": amount,
                                "requested_quantity": requested_quantity,
                                "state": ladder_state,
                                "tracker": recovery_result,
                            },
                        }
                        order_number += 1
                        trade, cash, flow, order_warnings = self._execute_order(
                            spec,
                            code,
                            bar,
                            PendingOrder(
                                code=code,
                                side="SELL",
                                source="RECOVERY_SELL",
                                signal_date=day,
                                execute_date=day,
                                reason="RECOVERY_RULE",
                                config={
                                    "lot_size": lot_size,
                                    "signal_evidence": signal_evidence,
                                    "cost_basis": "weighted_average",
                                },
                                requested_quantity=requested_quantity,
                                intraday_price=float(bar["close"]),
                            ),
                            tactical_book,
                            cash,
                            allocation,
                            lot_size,
                            next_day,
                            order_number,
                        )
                        trade["book"] = "tactical"
                        trades.append(trade)
                        warnings.extend(order_warnings)
                        if flow:
                            cash_flows.append(flow)

                exit_quantity = _layered_exit_quantity(
                    sell_basis,
                    tactical_book,
                    day,
                    float(bar["close"]),
                )
                plan = build_signal_plan(
                    spec,
                    bars,
                    index,
                    exit_quantity,
                    indicator_series=indicator_series,
                )

                if plan["entry_count"] >= 1 and not core_initialized and core_ratio > 0:
                    core_initialized = True
                    core_cash = max(0.0, float(allocation) * core_ratio)
                    core_evidence = dict(plan["evidence"])
                    core_evidence.update(
                        {
                            "book": "core",
                            "buy_cash": core_cash,
                            "core_ratio": core_ratio,
                        }
                    )
                    order_number += 1
                    trade, cash, flow, order_warnings = self._execute_order(
                        spec,
                        code,
                        bar,
                        PendingOrder(
                            code=code,
                            side="BUY",
                            source="CORE_BUY",
                            signal_date=day,
                            execute_date=day,
                            reason="CORE_FIRST_ENTRY",
                            config={
                                "type": "recurrent_cash",
                                "amount": core_cash,
                                "lot_size": lot_size,
                                "signal_evidence": core_evidence,
                                "cost_basis": "weighted_average",
                            },
                            intraday_price=float(bar["close"]),
                        ),
                        core_book,
                        cash,
                        allocation,
                        lot_size,
                        next_day,
                        order_number,
                    )
                    trade["book"] = "core"
                    trades.append(trade)
                    warnings.extend(order_warnings)
                    if flow:
                        cash_flows.append(flow)

                if plan["action"] == "BUY":
                    signal_evidence = dict(plan["evidence"])
                    buy_cash = float(plan["buy_cash"])
                    if ladder_state is not None:
                        ladder_result = resolve_ladder_level(
                            spec, ladder_state, triggered_levels
                        )
                        triggered_levels = set(ladder_result["triggered_levels"])
                        buy_cash = resolve_tactical_cash(
                            buy_cash, ladder_result["ladder_amount"]
                        )
                        signal_evidence["ladder"] = {
                            "state": ladder_state,
                            "new_levels": ladder_result["new_levels"],
                            "triggered_levels": ladder_result["triggered_levels"],
                            "ladder_amount": ladder_result["ladder_amount"],
                        }
                    elif fibonacci_state is not None:
                        buy_cash = resolve_tactical_cash(
                            buy_cash, fibonacci_result["ladder_amount"]
                        )
                        signal_evidence["fibonacci"] = {
                            "state": fibonacci_state,
                            "new_levels": fibonacci_result["new_levels"],
                            "level": fibonacci_result["level"],
                            "ladder_amount": fibonacci_result["ladder_amount"],
                        }
                    signal_evidence["book"] = "tactical"
                    signal_evidence["rsi_cash"] = float(plan["buy_cash"])
                    signal_evidence["buy_cash"] = buy_cash
                    order_number += 1
                    trade, cash, flow, order_warnings = self._execute_order(
                        spec,
                        code,
                        bar,
                        PendingOrder(
                            code=code,
                            side="BUY",
                            source="SIGNAL_BUY",
                            signal_date=day,
                            execute_date=day,
                            reason="ENTRY_RULE",
                            config={
                                "type": "recurrent_cash",
                                "amount": buy_cash,
                                "lot_size": lot_size,
                                "signal_evidence": signal_evidence,
                                "cost_basis": "weighted_average",
                            },
                            intraday_price=float(bar["close"]),
                        ),
                        tactical_book,
                        cash,
                        allocation,
                        lot_size,
                        next_day,
                        order_number,
                    )
                    trade["book"] = "tactical"
                    trades.append(trade)
                    warnings.extend(order_warnings)
                    if flow:
                        cash_flows.append(flow)
                elif plan["action"] == "SELL" and exit_mode == "rsi":
                    signal_evidence = dict(plan["evidence"])
                    signal_evidence["book"] = "tactical"
                    signal_evidence["sell_quantity"] = plan["sell_quantity"]
                    signal_evidence["sell_basis"] = sell_basis
                    signal_evidence["profit_tactical_quantity"] = (
                        tactical_book.available_profitable_quantity(
                            float(bar["close"]), day
                        )
                        if sell_basis == "profitable_tactical"
                        else None
                    )
                    order_number += 1
                    trade, cash, flow, order_warnings = self._execute_order(
                        spec,
                        code,
                        bar,
                        PendingOrder(
                            code=code,
                            side="SELL",
                            source="SELL",
                            signal_date=day,
                            execute_date=day,
                            reason="EXIT_RULE",
                            config={
                                "lot_size": lot_size,
                                "signal_evidence": signal_evidence,
                                "cost_basis": "weighted_average",
                                "sell_basis": sell_basis,
                            },
                            requested_quantity=plan["sell_quantity"],
                            intraday_price=float(bar["close"]),
                        ),
                        tactical_book,
                        cash,
                        allocation,
                        lot_size,
                        next_day,
                        order_number,
                    )
                    trade["book"] = "tactical"
                    trades.append(trade)
                    warnings.extend(order_warnings)
                    if flow:
                        cash_flows.append(flow)
                elif plan["action"] == "SELL" and exit_mode == "recovery":
                    skipped_sell_signals.append(
                        {
                            "signal_date": day,
                            "exit_count": plan["exit_count"],
                            "tactical_quantity": tactical_book.total_quantity(),
                            "reason": "RSI_EXIT_DISABLED_BY_RECOVERY_MODE",
                        }
                    )
                elif plan["exit_count"] > 0:
                    skipped_sell_signals.append(
                        {
                            "signal_date": day,
                            "exit_count": plan["exit_count"],
                            "tactical_quantity": tactical_book.total_quantity(),
                            "reason": "EXIT_WITHOUT_TACTICAL_POSITION",
                        }
                    )

            if core_book.quantity_before(day) + tactical_book.quantity_before(day) > 0:
                dividend = _as_float(bar.get("cash_dividend"))
                if dividend and dividend > 0:
                    eligible = core_book.quantity_before(day) + tactical_book.quantity_before(day)
                    cash += eligible * dividend

            core_value = core_book.market_value(float(bar["close"]))
            tactical_value = tactical_book.market_value(float(bar["close"]))
            equity_curve[day] = cash + core_value + tactical_value
            cash_curve[day] = cash
            market_value_curve[day] = core_value + tactical_value

        if core_book.total_quantity() > 0 or tactical_book.total_quantity() > 0:
            warnings.append(
                "{} positions remain at the end; valued at the final close".format(code)
            )
        last_day = dates[-1] if dates else ""
        last_price = float(bars[-1]["close"]) if bars else 0.0
        return {
            "trades": trades,
            "equity_curve": equity_curve,
            "cash_curve": cash_curve,
            "market_value_curve": market_value_curve,
            "exposure_curve": {
                day: (market_value_curve[day] / equity if equity else 0.0)
                for day, equity in equity_curve.items()
            },
            "warnings": warnings,
            "cash_flows": cash_flows,
            "skipped_sell_signals": skipped_sell_signals,
            "positions": _layered_position_snapshot(
                core_book, tactical_book, cash, last_price, last_day
            ),
        }

    def _run_portfolio(self, spec, data, scenario):
        codes = list(data)
        bars_by_code = {code: list(bars) for code, bars in data.items()}
        maps = {
            code: {str(bar["date"]): bar for bar in bars}
            for code, bars in bars_by_code.items()
        }
        dates = sorted({day for values in maps.values() for day in values})
        books = {code: PositionBook(code) for code in codes}
        pending: List[PendingOrder] = []
        last_close: Dict[str, float] = {}
        equity_curve: Dict[str, float] = {}
        cash_curve: Dict[str, float] = {}
        market_value_curve: Dict[str, float] = {}
        trades: List[Dict[str, Any]] = []
        warnings: List[str] = []
        cash_flows: List[Dict[str, Any]] = []
        cash = float(spec.initial_capital)
        event_maps = {code: _periodic_events(spec, bars_by_code[code]) for code in codes}
        indices = {code: 0 for code in codes}
        order_number = 0
        close_execution = _uses_close_execution(spec)
        indicator_series = {
            code: build_indicator_series(spec, bars_by_code[code])
            for code in codes
        } if close_execution else {}

        for day in dates:
            eligible_periodic = []
            for code in codes:
                event = event_maps[code].get(day, [])
                if books[code].total_quantity() > 0:
                    eligible_periodic.extend((code, item) for item in event)
            for code, event in eligible_periodic:
                config = dict(event["config"])
                if config.get("funding") == "external_contribution" and len(eligible_periodic) > 1:
                    if config.get("amount") is not None:
                        config["amount"] = float(config["amount"]) / len(eligible_periodic)
                pending.append(_periodic_order(code, {"planned_date": event["planned_date"], "config": config}, day))

            due = [order for order in pending if order.execute_date == day]
            pending = [order for order in pending if order.execute_date != day]
            due.sort(key=lambda order: _order_sort_key(spec, order, codes.index(order.code)))
            for order in due:
                bar = maps[order.code].get(day)
                if bar is None:
                    continue
                order_number += 1
                next_day = _next_date(sorted(maps[order.code]), day)
                trade, cash, flow, order_warnings = self._execute_order(
                    spec,
                    order.code,
                    bar,
                    order,
                    books[order.code],
                    cash,
                    spec.initial_capital,
                    _lot_size(spec),
                    next_day,
                    order_number,
                )
                trades.append(trade)
                if flow:
                    cash_flows.append(flow)
                warnings.extend(order_warnings)

            for code in codes:
                bar = maps[code].get(day)
                if bar is None:
                    continue
                index = indices[code]
                indices[code] += 1
                last_close[code] = float(bar["close"])
                book = books[code]
                if book.quantity_before(day) > 0:
                    dividend = _as_float(bar.get("cash_dividend"))
                    if dividend and dividend > 0:
                        cash += book.quantity_before(day) * dividend

                suspended = bool(bar.get("suspended") or bar.get("is_suspended"))
                if book.total_quantity() > 0 and not suspended:
                    stop_take = _stop_take_trigger(
                        spec, bar, book.average_cost(), scenario
                    )
                    if stop_take is not None:
                        reason, exit_price = stop_take
                        order_number += 1
                        trade, cash, flow, order_warnings = self._execute_order(
                            spec,
                            code,
                            bar,
                            PendingOrder(
                                code=code,
                                side="SELL",
                                source=reason,
                                signal_date=day,
                                execute_date=day,
                                reason=reason,
                                requested_quantity=book.available_quantity(day),
                                intraday_price=exit_price,
                            ),
                            book,
                            cash,
                            spec.initial_capital,
                            _lot_size(spec),
                            _next_date(sorted(maps[code]), day),
                            order_number,
                        )
                        trades.append(trade)
                        if flow:
                            cash_flows.append(flow)
                        warnings.extend(order_warnings)

                if suspended:
                    warnings.append("{} {} 停牌，无法产生或执行交易".format(code, day))
                    continue
                next_day = _next_date(sorted(maps[code]), day)
                if close_execution:
                    plan = build_signal_plan(
                        spec,
                        bars_by_code[code],
                        index,
                        book.total_quantity(),
                        indicator_series=indicator_series[code],
                    )
                    if plan["action"] in {"BUY", "SELL"}:
                        order_number += 1
                        action = plan["action"]
                        trade, cash, flow, order_warnings = self._execute_order(
                            spec,
                            code,
                            bar,
                            PendingOrder(
                                code=code,
                                side=action,
                                source="SIGNAL_BUY" if action == "BUY" else "SELL",
                                signal_date=day,
                                execute_date=day,
                                reason=plan.get("evidence", {}).get("reason", "SIGNAL"),
                                config={
                                    "type": "recurrent_cash",
                                    "amount": plan["buy_cash"],
                                    "lot_size": _lot_size(spec),
                                    "cost_basis": "weighted_average",
                                },
                                requested_quantity=(
                                    plan["sell_quantity"] if action == "SELL" else None
                                ),
                                intraday_price=float(bar["close"]),
                                evidence=plan["evidence"],
                            ),
                            book,
                            cash,
                            spec.initial_capital,
                            _lot_size(spec),
                            next_day,
                            order_number,
                        )
                        trades.append(trade)
                        if flow:
                            cash_flows.append(flow)
                        warnings.extend(order_warnings)
                    continue

                closes = [float(item["close"]) for item in bars_by_code[code][:index + 1]]
                entry_match = _rules_match(spec.entry, index, closes)
                exit_match = _rules_match(spec.exit, index, closes)
                if book.total_quantity() == 0 and entry_match and next_day:
                    _queue_next_order(
                        pending,
                        PendingOrder(
                            code=code,
                            side="BUY",
                            source="SIGNAL_BUY",
                            signal_date=day,
                            execute_date=next_day,
                            reason="ENTRY_RULE",
                            config=dict(spec.position_sizing or {}),
                        ),
                    )
                elif book.total_quantity() > 0:
                    if exit_match and next_day:
                        _queue_next_order(
                            pending,
                            PendingOrder(
                                code=code,
                                side="SELL",
                                source="SELL",
                                signal_date=day,
                                execute_date=next_day,
                                reason="EXIT_RULE",
                            ),
                        )
                    signal_add = _signal_add_config(spec)
                    if entry_match and signal_add and next_day:
                        _queue_next_order(
                            pending,
                            PendingOrder(
                                code=code,
                                side="BUY",
                                source="SIGNAL_BUY",
                                signal_date=day,
                                execute_date=next_day,
                                reason="ENTRY_RULE_ADD",
                                config=signal_add,
                            ),
                        )

            market_value = sum(
                books[code].market_value(last_close[code])
                for code in last_close
            )
            equity_curve[day] = cash + market_value
            cash_curve[day] = cash
            market_value_curve[day] = market_value

        for code, book in books.items():
            if book.total_quantity() > 0:
                warnings.append("{} 数据结束时仍有未平仓头寸，按最后收盘价估值".format(code))
        return {
            "scenario": scenario,
            "equity_curve": equity_curve,
            "cash_curve": cash_curve,
            "market_value_curve": market_value_curve,
            "exposure_curve": {
                day: (market_value_curve[day] / equity if equity else 0.0)
                for day, equity in equity_curve.items()
            },
            "trades": trades,
            "cash_flows": cash_flows,
            "positions": {code: _position_snapshot(book) for code, book in books.items()},
            "metrics": _metrics(
                spec.initial_capital,
                equity_curve,
                list(equity_curve.values()),
                trades,
                cash_flows,
                {code: _position_snapshot(book) for code, book in books.items()},
                risk_free_rate_annual=spec.risk_free_rate_annual,
                cash_curve=cash_curve,
                market_value_curve=market_value_curve,
            ),
            "warnings": sorted(set(warnings)),
        }

    def _execute_order(
        self,
        spec,
        code,
        bar,
        order,
        book,
        cash,
        allocation,
        lot_size,
        next_day,
        order_number,
    ):
        day = str(bar["date"])
        raw_price = float(order.intraday_price if order.intraday_price is not None else bar["open"])
        rates = _cost_profile(spec)
        cash_before = cash
        flow = None
        warnings = []
        order_config = order.config or {}
        signal_evidence = order.evidence or order_config.get("signal_evidence")
        if order.funding == "external_contribution":
            amount = _as_float(order.amount)
            if amount and amount > 0:
                cash += amount
                flow = {
                    "type": "external_contribution",
                    "date": day,
                    "code": code,
                    "amount": round(amount, 8),
                    "source": order.source,
                }
        blocked = _blocked_reason(bar, order.side, raw_price)
        order_id = "{}-{}-{}".format(code, day, order_number)
        position_before = book.total_quantity()
        available_before = book.available_quantity(day)
        sell_basis = str(order_config.get("sell_basis", "all_tactical"))
        sell_available_before = available_before
        if order.side == "SELL" and sell_basis == "profitable_tactical":
            sell_available_before = book.available_profitable_quantity(raw_price, day)
        requested_sell = order.requested_quantity
        if order.side == "SELL" and requested_sell is None:
            requested_sell = _sell_quantity(spec, book, day, lot_size)
        requested_buy = order.requested_quantity
        if order.side == "BUY" and requested_buy is None:
            config = order_config or dict(spec.position_sizing or {})
            requested_buy = _buy_requested_quantity(config, lot_size)
            if requested_buy is None:
                fill_price = raw_price * (1 + float(rates["slippage_rate"]))
                target_cash = _buy_target_cash(config, cash, allocation)
                requested_buy = floor(
                    max(0.0, target_cash) / fill_price / lot_size
                ) * lot_size if fill_price > 0 else 0
        if blocked:
            return (
                _unfilled_trade(
                    code,
                    order.side,
                    day,
                    order.signal_date,
                    raw_price,
                    requested_sell if order.side == "SELL" else requested_buy or 0,
                    "{}；订单未成交".format(blocked),
                    order_id=order_id,
                    source=order.source,
                    requested_quantity=(
                        requested_sell
                        if order.side == "SELL"
                        else requested_buy
                    ),
                    position_before=position_before,
                    position_after=position_before,
                    available_quantity_before=available_before,
                    cash_before=cash_before,
                    cash_after=cash,
                ),
                cash,
                flow,
                ["{} {} {}：订单未成交".format(code, day, blocked)],
            )

        if order.side == "BUY":
            config = order_config or dict(spec.position_sizing or {})
            requested = requested_buy
            fill_price = raw_price * (1 + float(rates["slippage_rate"]))
            target_cash = _buy_target_cash(config, cash, allocation)
            max_quantity = _affordable_quantity(rates, fill_price, cash, lot_size)
            if requested is None:
                requested = floor(target_cash / fill_price / lot_size) * lot_size
                target_quantity = max_quantity if target_cash >= cash else _affordable_quantity(
                    rates, fill_price, min(cash, target_cash), lot_size
                )
            else:
                target_quantity = min(requested, max_quantity)
            target_quantity = floor(max(0, target_quantity) / lot_size) * lot_size
            if target_quantity <= 0:
                return (
                    _unfilled_trade(
                        code,
                        "BUY",
                        day,
                        order.signal_date,
                        raw_price,
                        requested or 0,
                        "资金不足或不足一个交易单位，订单未成交",
                        order_id=order_id,
                        source=order.source,
                        requested_quantity=requested,
                        position_before=position_before,
                        position_after=position_before,
                        available_quantity_before=available_before,
                        cash_before=cash_before,
                        cash_after=cash,
                    ),
                    cash,
                    flow,
                    ["{} {} 买入资金不足或不足一个交易单位".format(code, day)],
                )
            gross = target_quantity * fill_price
            fee_breakdown = _fee_breakdown(rates, fill_price, target_quantity, gross)
            fee = sum(fee_breakdown.values())
            total = gross + fee
            if total > cash:
                target_quantity = _affordable_quantity(rates, fill_price, cash, lot_size)
                gross = target_quantity * fill_price
                fee_breakdown = _fee_breakdown(rates, fill_price, target_quantity, gross)
                fee = sum(fee_breakdown.values())
                total = gross + fee
            if target_quantity <= 0 or total > cash:
                return (
                    _unfilled_trade(
                        code,
                        "BUY",
                        day,
                        order.signal_date,
                        raw_price,
                        requested or 0,
                        "资金不足，订单未成交",
                        order_id=order_id,
                        source=order.source,
                        requested_quantity=requested,
                        position_before=position_before,
                        position_after=position_before,
                        available_quantity_before=available_before,
                        cash_before=cash_before,
                        cash_after=cash,
                    ),
                    cash,
                    flow,
                    ["{} {} 买入资金不足".format(code, day)],
                )
            cash -= total
            lot_id = order_id + "-lot"
            book.add(
                PositionLot(
                    lot_id=lot_id,
                    code=code,
                    buy_date=day,
                    available_date=next_day or "9999-12-31",
                    quantity=target_quantity,
                    price=fill_price,
                    cost=total,
                    source=order.source,
                    fees=fee,
                )
            )
            status = "PARTIAL" if requested is not None and target_quantity < requested else "FILLED"
            trade = {
                "order_id": order_id,
                "code": code,
                "side": "BUY",
                "source": order.source,
                "date": day,
                "signal_date": order.signal_date,
                "price": round(fill_price, 8),
                "raw_price": raw_price,
                "quantity": target_quantity,
                "requested_quantity": requested,
                "filled_quantity": target_quantity,
                "fee": round(fee, 8),
                "commission": round(fee_breakdown["commission"], 8),
                "stamp_duty": round(fee_breakdown["stamp_duty"], 8),
                "transfer_fee": round(fee_breakdown["transfer_fee"], 8),
                "slippage_impact": round(
                    max(0.0, (fill_price - raw_price) * target_quantity), 8
                ),
                "position_before": position_before,
                "position_after": book.total_quantity(),
                "available_quantity_before": available_before,
                "cash_before": cash_before,
                "cash_after": round(cash, 8),
                "lot_id": lot_id,
                "lot_ids": [lot_id],
                "status": status,
                "reason": order.reason,
            }
            if signal_evidence is not None:
                trade["signal_evidence"] = signal_evidence
            if status == "PARTIAL":
                warnings.append("{} {} 买入数量因资金不足被截断".format(code, day))
            return trade, cash, flow, warnings

        requested = requested_sell
        if requested <= 0 or sell_available_before <= 0:
            return (
                _unfilled_trade(
                    code,
                    "SELL",
                    day,
                    order.signal_date,
                    raw_price,
                    requested,
                    "没有可卖持仓，订单未成交",
                    order_id=order_id,
                    source=order.source,
                    requested_quantity=requested,
                    position_before=position_before,
                    position_after=position_before,
                    available_quantity_before=available_before,
                    eligible_quantity_before=sell_available_before,
                    cash_before=cash_before,
                    cash_after=cash,
                ),
                cash,
                flow,
                ["{} {} 没有可卖持仓".format(code, day)],
            )
        target_quantity = floor(min(requested, sell_available_before) / lot_size) * lot_size
        if target_quantity <= 0:
            return (
                _unfilled_trade(
                    code,
                    "SELL",
                    day,
                    order.signal_date,
                    raw_price,
                    requested,
                    "卖出数量不足一个交易单位，订单未成交",
                    order_id=order_id,
                    source=order.source,
                    requested_quantity=requested,
                    position_before=position_before,
                    position_after=position_before,
                    available_quantity_before=available_before,
                    eligible_quantity_before=sell_available_before,
                    cash_before=cash_before,
                    cash_after=cash,
                ),
                cash,
                flow,
                ["{} {} 卖出数量不足一个交易单位".format(code, day)],
            )
        fill_price = raw_price * (1 - float(rates["slippage_rate"]))
        average_cost_before = book.average_cost()
        if sell_basis == "profitable_tactical":
            actual, allocations = book.sell_profitable(
                target_quantity, raw_price, day
            )
        else:
            actual, allocations = book.sell(target_quantity, day)
        gross = actual * fill_price
        fee_breakdown = _fee_breakdown(rates, fill_price, actual, gross, side="SELL")
        fee = sum(fee_breakdown.values())
        revenue = gross - fee
        cash += revenue
        if (
            order_config.get("cost_basis") == "weighted_average"
            and sell_basis != "profitable_tactical"
            and average_cost_before is not None
        ):
            allocated_cost = average_cost_before * actual
        else:
            allocated_cost = sum(item["allocated_cost"] for item in allocations)
        clipped = actual < requested
        status = "PARTIAL" if clipped else "FILLED"
        if clipped:
            warnings.append(
                "{} {} 请求卖出 {}，实际可卖 {}，已按可卖数量截断".format(
                    code, day, requested, actual
                )
            )
        trade = {
            "order_id": order_id,
            "code": code,
            "side": "SELL",
            "source": order.source,
            "date": day,
            "signal_date": order.signal_date,
            "price": round(fill_price, 8),
            "quantity": actual,
            "requested_quantity": requested,
            "filled_quantity": actual,
            "fee": round(fee, 8),
            "commission": round(fee_breakdown["commission"], 8),
            "stamp_duty": round(fee_breakdown["stamp_duty"], 8),
            "transfer_fee": round(fee_breakdown["transfer_fee"], 8),
            "slippage_impact": round(
                max(0.0, (raw_price - fill_price) * actual), 8
            ),
            "pnl": round(revenue - allocated_cost, 8),
            "position_before": position_before,
            "position_after": book.total_quantity(),
            "available_quantity_before": available_before,
            "eligible_quantity_before": sell_available_before,
            "cash_before": cash_before,
            "cash_after": round(cash, 8),
            "lot_ids": [item["lot_id"] for item in allocations],
            "allocations": allocations,
            "holding_days": (
                sum(item["quantity"] * item["holding_days"] for item in allocations)
                / actual
                if actual
                else None
            ),
            "status": status,
            "reason": order.reason,
        }
        if signal_evidence is not None:
            trade["signal_evidence"] = signal_evidence
        return trade, cash, flow, warnings

    def _validation_summary(self, spec, data, scenarios):
        dates = sorted({str(bar["date"]) for bars in data.values() for bar in bars})
        ratio = float(spec.validation.get("split_ratio", 0.7))
        ratio = min(max(ratio, 0.01), 0.99)
        split_index = min(max(int(len(dates) * ratio), 1), max(len(dates) - 1, 1))
        train_dates = set(dates[:split_index])
        test_dates = set(dates[split_index:])
        train_data = {
            code: [bar for bar in bars if str(bar["date"]) in train_dates]
            for code, bars in data.items()
        }
        test_data = {
            code: [bar for bar in bars if str(bar["date"]) in test_dates]
            for code, bars in data.items()
        }
        split = {
            "ratio": ratio,
            "train_start": dates[0] if train_dates else None,
            "train_end": dates[split_index - 1] if train_dates else None,
            "test_start": dates[split_index] if test_dates else None,
            "test_end": dates[-1] if test_dates else None,
            "train": {},
            "test": {},
        }
        if train_dates and test_dates:
            for scenario in scenarios:
                split["train"][scenario] = _metrics_from_data(
                    self, spec, train_data, scenario
                )
                split["test"][scenario] = _metrics_from_data(
                    self, spec, test_data, scenario
                )
        return {
            "sample_split": split,
            "rolling": {
                "train_years": int(spec.validation.get("rolling_train_years", 3)),
                "test_years": int(spec.validation.get("rolling_test_years", 1)),
                "windows": _rolling_windows(dates, spec.validation),
            },
        }


def _periodic_events(spec, bars):
    sizing = spec.position_sizing or {}
    config = (sizing.get("while_holding") or {}).get("periodic") or {}
    if not config or not config.get("enabled", True):
        return {}
    bar_dates = sorted(str(bar["date"]) for bar in bars)
    if not bar_dates:
        return {}
    first = date.fromisoformat(bar_dates[0])
    last = date.fromisoformat(bar_dates[-1])
    frequency = config.get("frequency")
    planned_dates = []
    if frequency == "dates":
        planned_dates = [date.fromisoformat(str(value)) for value in config.get("dates", [])]
    else:
        current = first
        while current <= last:
            if frequency == "weekly" and current.weekday() == int(config.get("weekday", 0)):
                planned_dates.append(current)
            elif frequency == "monthly" and current.day == int(config.get("day", 1)):
                planned_dates.append(current)
            current += timedelta(days=1)

    result = {}
    bar_date_set = set(bar_dates)
    for planned in sorted(planned_dates):
        planned_text = planned.isoformat()
        if planned_text in bar_date_set:
            base = planned_text
        elif config.get("non_trading_day", "skip") == "next_trading_day":
            base = next((value for value in bar_dates if value > planned_text), None)
        else:
            continue
        if base is None:
            continue
        execution = (
            base
            if config.get("execution", "next_open") == "scheduled_open"
            else _next_date(bar_dates, base)
        )
        if execution is not None:
            result.setdefault(execution, []).append(
                {"planned_date": planned_text, "config": dict(config)}
            )
    return result


def _periodic_order(code, event, execute_date):
    config = dict(event["config"])
    return PendingOrder(
        code=code,
        side="BUY",
        source="PERIODIC_BUY",
        signal_date=str(event["planned_date"]),
        execute_date=str(execute_date),
        reason="PERIODIC_DCA",
        config=config,
        requested_quantity=_buy_requested_quantity(config, _config_lot_size(config)),
        amount=_as_float(config.get("amount", config.get("cash"))),
        funding=str(config.get("funding", "existing_cash")),
    )


def _config_lot_size(config):
    try:
        return max(1, int(config.get("lot_size", DEFAULT_LOT_SIZE)))
    except (TypeError, ValueError):
        return DEFAULT_LOT_SIZE


def _signal_add_config(spec):
    config = (spec.position_sizing or {}).get("while_holding", {}).get("signal_add")
    if not isinstance(config, dict) or not config.get("enabled", True):
        return None
    return dict(config)


def _queue_next_order(pending, order):
    for existing in pending:
        if (
            existing.code == order.code
            and existing.side == order.side
            and existing.source == order.source
            and existing.execute_date == order.execute_date
        ):
            return
    pending.append(order)


def _order_sort_key(spec, order, code_index):
    priority = [
        {"EXIT": "SELL", "ENTRY": "SIGNAL_BUY"}.get(
            str(value).upper(), str(value).upper()
        )
        for value in (spec.action_priority or [])
    ]
    if order.priority is not None:
        try:
            rank = int(order.priority)
        except (TypeError, ValueError):
            rank = len(priority)
        return rank, code_index, order.signal_date, order.source
    action = order.source if order.source in {"PERIODIC_BUY", "SIGNAL_BUY"} else order.side
    action = {"EXIT": "SELL", "ENTRY": "SIGNAL_BUY"}.get(action, action)
    try:
        rank = priority.index(action)
    except ValueError:
        rank = len(priority)
    return rank, code_index, order.signal_date, order.source


def _next_date(dates, current):
    return next((value for value in sorted(dates) if value > str(current)), None)


def _position_snapshot(book):
    return [
        {
            "lot_id": lot.lot_id,
            "code": lot.code,
            "buy_date": lot.buy_date,
            "available_date": lot.available_date,
            "quantity": lot.quantity,
            "price": round(lot.price, 8),
            "cost": round(lot.cost, 8),
            "fees": round(lot.fees, 8),
            "source": lot.source,
        }
        for lot in book.lots
        if lot.quantity > 0
    ]


def _layered_book_snapshot(book, price, as_of):
    average_cost = book.average_cost()
    return {
        "quantity": book.total_quantity(),
        "available_quantity": book.available_quantity(as_of) if as_of else 0,
        "average_cost": round(average_cost, 8) if average_cost is not None else None,
        "market_value": round(book.market_value(price), 8),
        "lots": _position_snapshot(book),
    }


def _layered_position_snapshot(core_book, tactical_book, cash, price, as_of):
    core = _layered_book_snapshot(core_book, price, as_of)
    tactical = _layered_book_snapshot(tactical_book, price, as_of)
    return {
        "core": core,
        "tactical": tactical,
        "total_quantity": core["quantity"] + tactical["quantity"],
        "core_market_value": core["market_value"],
        "tactical_market_value": tactical["market_value"],
        "cash": round(cash, 8),
    }


def _buy_requested_quantity(config, lot_size):
    if str(config.get("type", "all_in")) != "fixed_quantity":
        return None
    try:
        return floor(int(config.get("quantity", 0)) / lot_size) * lot_size
    except (TypeError, ValueError):
        return 0


def _recovery_sell_quantity(amount, price, lot_size):
    if float(amount) <= 0 or float(price) <= 0:
        return 0
    return floor(float(amount) / float(price) / lot_size) * lot_size


def _layered_exit_quantity(sell_basis, book, day, price):
    if sell_basis == "profitable_tactical":
        return book.available_profitable_quantity(price, day)
    return book.total_quantity()


def _buy_target_cash(config, cash, allocation):
    kind = str(config.get("type", "all_in"))
    if kind == "recurrent_cash":
        return min(cash, float(config.get("amount", config.get("cash", 0.0))))
    if kind == "fixed_cash":
        return min(cash, float(config.get("cash", config.get("amount", cash))))
    if kind in {"cash_pct", "fixed_fraction"}:
        fraction = float(config.get("fraction", config.get("amount", 1.0)))
        return max(0.0, cash * fraction)
    if kind == "fixed_quantity":
        return cash
    return max(0.0, min(cash, allocation))


def _affordable_quantity(rates, price, cash, lot_size):
    if cash <= 0 or price <= 0:
        return 0
    quantity = floor(cash / price / lot_size) * lot_size
    while quantity > 0:
        gross = quantity * price
        if gross + _fee(rates, price, quantity, gross) <= cash + 1e-9:
            return quantity
        quantity -= lot_size
    return 0


def _sell_quantity(spec, book, day, lot_size):
    available = book.available_quantity(day)
    total = book.total_quantity()
    sell = (spec.exit or {}).get("sell") or {}
    kind = sell.get("type", "all")
    if kind == "percent":
        return floor(total * float(sell.get("value", 1.0)) / lot_size) * lot_size
    if kind == "quantity":
        return max(0, int(sell.get("value", 0)))
    return available


def _uses_close_execution(spec):
    execution = getattr(spec, "execution", {}) or {}
    sizing = spec.position_sizing or {}
    return (
        execution.get("signal_at") == "close"
        and execution.get("fill_at") == "close"
        and sizing.get("type") == "recurrent_cash"
        and (spec.entry or {}).get("mode") == "count_conditions"
    )


def _uses_layered_close_execution(spec):
    sizing = spec.position_sizing or {}
    return _uses_close_execution(spec) and bool(
        sizing.get("core")
        or sizing.get("drawdown_ladder")
        or sizing.get("fibonacci_ladder")
    )


def _normalize_data(spec, data):
    normalized = {}
    warnings = []
    actions = []
    source = {}
    for raw_code, bars in (data.items() if isinstance(data, dict) else []):
        code = str(raw_code)
        try:
            code, _ = normalize_ticker(code)
        except ValueError:
            pass
        source[code] = bars
    for code in spec.universe:
        raw_bars = source.get(code)
        if raw_bars is None:
            warnings.append("{} 缺失全部历史数据".format(code))
            continue
        valid = []
        for raw_bar in raw_bars:
            if not isinstance(raw_bar, dict):
                warnings.append("{} 存在非对象日线，已跳过".format(code))
                continue
            missing = []
            if "date" not in raw_bar:
                missing.append("date")
            values = {field: _price_value(raw_bar, field) for field in REQUIRED_BAR_FIELDS[1:]}
            missing.extend(field for field, value in values.items() if value is None)
            if missing:
                warnings.append(
                    "{} {} 缺失字段：{}，该日已跳过".format(
                        code, raw_bar.get("date", "未知日期"), ",".join(sorted(set(missing)))
                    )
                )
                continue
            bar = dict(raw_bar)
            bar.update(values)
            bar["date"] = str(raw_bar["date"])
            valid.append(bar)
            action = raw_bar.get("corporate_action") or raw_bar.get("corporate_actions")
            if action:
                actions.append({"code": code, "date": bar["date"], "action": action})
        valid.sort(key=lambda item: item["date"])
        if valid:
            normalized[code] = valid
        else:
            warnings.append("{} 没有可用的完整日线".format(code))
    return normalized, warnings, actions


def _metrics(
    initial: float,
    equity_curve: Dict[str, float],
    values: List[float],
    trades: List[Dict[str, Any]],
    cash_flows: Optional[List[Dict[str, Any]]] = None,
    positions: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    risk_free_rate_annual: Optional[float] = None,
    cash_curve: Optional[Dict[str, float]] = None,
    market_value_curve: Optional[Dict[str, float]] = None,
):
    metrics = calculate_performance_metrics(
        initial=initial,
        equity_curve=equity_curve,
        trades=trades,
        cash_flows=cash_flows,
        risk_free_rate_annual=risk_free_rate_annual,
        cash_curve=cash_curve,
        market_value_curve=market_value_curve,
    )
    position_metrics = _position_metrics(
        positions, max(equity_curve.keys()) if equity_curve else None
    )
    return {**metrics, **position_metrics}


def _position_metrics(positions, as_of):
    lots = []
    layered = {}
    for code, code_lots in (positions or {}).items():
        if isinstance(code_lots, dict) and (
            "core" in code_lots or "tactical" in code_lots
        ):
            layered[code] = code_lots
            for book_name in ("core", "tactical"):
                book = code_lots.get(book_name) or {}
                for raw_lot in book.get("lots", []) or []:
                    lot = dict(raw_lot)
                    lot.setdefault("code", code)
                    lot.setdefault("book", book_name)
                    lots.append(lot)
            continue
        for raw_lot in code_lots or []:
            lot = dict(raw_lot)
            lot.setdefault("code", code)
            lots.append(lot)
    quantity = sum(max(0, int(lot.get("quantity", 0))) for lot in lots)
    available = sum(
        max(0, int(lot.get("quantity", 0)))
        for lot in lots
        if as_of is not None and str(lot.get("available_date", "9999-12-31")) <= str(as_of)
    )
    result = {
        "current_position_lots": lots,
        "current_position_quantity": quantity,
        "current_available_quantity": available,
    }
    if layered:
        core_quantity = sum(
            int((book.get("core") or {}).get("quantity", 0))
            for book in layered.values()
        )
        tactical_quantity = sum(
            int((book.get("tactical") or {}).get("quantity", 0))
            for book in layered.values()
        )
        result.update(
            {
                "core_position_quantity": core_quantity,
                "tactical_position_quantity": tactical_quantity,
                "core_market_value": round(
                    sum(float(book.get("core_market_value", 0.0)) for book in layered.values()),
                    8,
                ),
                "tactical_market_value": round(
                    sum(
                        float(book.get("tactical_market_value", 0.0))
                        for book in layered.values()
                    ),
                    8,
                ),
                "layered_cash": round(
                    sum(float(book.get("cash", 0.0)) for book in layered.values()),
                    8,
                ),
            }
        )
    return result


def _metrics_from_data(engine, spec, data, scenario):
    usable = {code: bars for code, bars in data.items() if bars}
    if not usable:
        return None
    return engine._run_scenario(spec, usable, scenario)["metrics"]


def _rolling_windows(dates, validation):
    if not dates:
        return []
    train_years = int(validation.get("rolling_train_years", 3))
    test_years = int(validation.get("rolling_test_years", 1))
    years = sorted({int(str(day)[:4]) for day in dates if str(day)[:4].isdigit()})
    windows = []
    for start in years:
        train_end = start + train_years - 1
        test_end = train_end + test_years
        if test_end > years[-1]:
            continue
        windows.append(
            {
                "train_start": str(start),
                "train_end": str(train_end),
                "test_start": str(train_end + 1),
                "test_end": str(test_end),
            }
        )
    return windows


def _price_value(bar, field):
    for key in ("adj_" + field, field + "_adj"):
        value = _as_float(bar.get(key))
        if value is not None:
            return value
    raw = _as_float(bar.get("raw_" + field))
    factor = _as_float(bar.get("adj_factor"))
    if raw is not None and factor is not None:
        return raw * factor
    return _as_float(bar.get(field))


def _provenance(spec, data, actions):
    dates = [str(bar["date"]) for bars in data.values() for bar in bars]
    policy = spec.data_policy
    provenance = {
        "source_name": policy.get("source_name", "a-stock-data"),
        "source_url": policy.get("source_url"),
        "source_version": policy.get("source_version", "a-stock-data:unknown"),
        "skill_name": policy.get("skill_name", "a-stock-data"),
        "skill_version": policy.get("skill_version"),
        "data_start": min(dates) if dates else None,
        "data_end": max(dates) if dates else None,
        "frequency": spec.frequency,
        "price_basis": policy.get("price_basis", "adjusted"),
        "execution": dict(getattr(spec, "execution", {}) or {}),
        "corporate_actions": actions,
        "cost_profile": _cost_profile(spec, include_defaults=True),
    }
    sizing = spec.position_sizing or {}
    if (
        sizing.get("core")
        or sizing.get("drawdown_ladder")
        or sizing.get("fibonacci_ladder")
    ):
        provenance["layered"] = {
            "exit_mode": str(sizing.get("exit_mode", "rsi")),
            "sell_basis": str(sizing.get("sell_basis", "all_tactical")),
            "core": dict(sizing.get("core") or {}),
            "drawdown_ladder": dict(sizing.get("drawdown_ladder") or {}),
            "fibonacci_ladder": dict(sizing.get("fibonacci_ladder") or {}),
        }
    return provenance


def _cost_profile(spec, include_defaults=False):
    profile = dict(spec.cost_profile or {})
    if include_defaults:
        profile.setdefault("template", "theoretical")
        profile.setdefault("version", "1.0.0")
    profile.setdefault("commission_rate", 0.0)
    profile.setdefault("stamp_duty_rate", 0.0)
    profile.setdefault("transfer_fee_rate", 0.0)
    profile.setdefault("slippage_rate", 0.0)
    profile.setdefault("minimum_commission", 0.0)
    return profile


def _fee(rates, price, quantity, gross, side="BUY"):
    return sum(_fee_breakdown(rates, price, quantity, gross, side).values())


def _fee_breakdown(rates, price, quantity, gross, side="BUY"):
    commission = max(
        gross * float(rates.get("commission_rate", 0.0)),
        float(rates.get("minimum_commission", 0.0)) if gross > 0 else 0.0,
    )
    transfer = gross * float(rates.get("transfer_fee_rate", 0.0))
    stamp = gross * float(rates.get("stamp_duty_rate", 0.0)) if side == "SELL" else 0.0
    return {
        "commission": commission,
        "stamp_duty": stamp,
        "transfer_fee": transfer,
    }


def _unfilled_trade(
    code,
    side,
    day,
    signal_date,
    price,
    quantity,
    reason,
    **fields,
):
    trade = {
        "code": code,
        "side": side,
        "date": day,
        "signal_date": signal_date,
        "price": round(price, 8),
        "quantity": quantity,
        "requested_quantity": quantity,
        "filled_quantity": 0,
        "lot_ids": [],
        "status": "UNFILLED",
        "reason": reason,
    }
    trade.update({key: value for key, value in fields.items() if value is not None})
    return trade


def _blocked_reason(bar, side, price):
    if bar.get("suspended") or bar.get("is_suspended"):
        return "停牌"
    if bar.get("unfillable"):
        return "数据标记为无法成交"
    if side == "BUY" and _at_limit(bar, "limit_up", price):
        return "涨停"
    if side == "SELL" and _at_limit(bar, "limit_down", price):
        return "跌停"
    if side == "BUY" and bar.get("is_limit_up"):
        return "涨停"
    if side == "SELL" and bar.get("is_limit_down"):
        return "跌停"
    return None


def _at_limit(bar, field, price):
    limit = bar.get(field)
    if isinstance(limit, bool) or limit is None:
        return False
    value = _as_float(limit)
    return value is not None and price >= value if field == "limit_up" else value is not None and price <= value


def _stop_take_trigger(spec, bar, entry_price, scenario):
    stop = None
    take = None
    if spec.stop_loss_pct is not None:
        stop_level = entry_price * (1 - float(spec.stop_loss_pct))
        if float(bar["open"]) <= stop_level:
            stop = float(bar["open"])
        elif float(bar["low"]) <= stop_level:
            stop = stop_level
    if spec.take_profit_pct is not None:
        take_level = entry_price * (1 + float(spec.take_profit_pct))
        if float(bar["open"]) >= take_level:
            take = float(bar["open"])
        elif float(bar["high"]) >= take_level:
            take = take_level
    if stop is None and take is None:
        return None
    if stop is not None and take is not None:
        reason = "STOP_LOSS" if scenario == "stop_first" else "TAKE_PROFIT"
        price = stop if reason == "STOP_LOSS" else take
        return reason, price
    return ("STOP_LOSS", stop) if stop is not None else ("TAKE_PROFIT", take)


def _lot_size(spec):
    value = (spec.position_sizing or {}).get("lot_size", DEFAULT_LOT_SIZE)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_LOT_SIZE


def _rules_match(section: Dict[str, Any], index: int, closes: List[float]) -> bool:
    rules = section.get("rules") or []
    if not rules:
        return False
    return all(_rule_match(rule, index, closes) for rule in rules)


def _rule_match(rule: Dict[str, Any], index: int, closes: List[float]) -> bool:
    kind = rule.get("type", "state")
    current_left = _indicator(rule.get("left"), index, closes)
    current_right = _indicator(rule.get("right"), index, closes)
    if current_left is None or current_right is None:
        return False
    if kind == "state":
        return _compare(current_left, current_right, rule.get("operator", ">"))
    if index == 0:
        return False
    previous_left = _indicator(rule.get("left"), index - 1, closes)
    previous_right = _indicator(rule.get("right"), index - 1, closes)
    if previous_left is None or previous_right is None:
        return False
    if kind == "cross_above":
        return previous_left <= previous_right and current_left > current_right
    if kind == "cross_below":
        return previous_left >= previous_right and current_left < current_right
    raise ValueError("不支持的规则类型：{}".format(kind))


def _indicator(name: Any, index: int, closes: List[float]) -> Optional[float]:
    if isinstance(name, (int, float)):
        return float(name)
    if name == "close":
        return closes[index]
    if isinstance(name, str) and name.startswith("sma_"):
        window = int(name.split("_", 1)[1])
        if index + 1 < window:
            return None
        return sum(closes[index - window + 1 : index + 1]) / window
    raise ValueError("不支持的指标：{}".format(name))


def _compare(left: float, right: float, operator: str) -> bool:
    return {
        ">": left > right,
        ">=": left >= right,
        "<": left < right,
        "<=": left <= right,
        "==": left == right,
    }.get(operator, False)


def _as_float(value):
    try:
        return float(value) if value not in (None, "", "--", "-") else None
    except (TypeError, ValueError):
        return None
