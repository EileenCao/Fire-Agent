"""Deterministic daily-bar engine with explicit A-share execution constraints."""

from math import floor
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mcp_server.domain.identifiers import normalize_ticker


REQUIRED_BAR_FIELDS = ("date", "open", "high", "low", "close")
DEFAULT_LOT_SIZE = 100


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
        allocation = spec.initial_capital / max(1, len(data))
        total_equity: Dict[str, float] = {}
        all_trades: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for code, bars in data.items():
            outcome = self._run_symbol(spec, code, bars, allocation, scenario)
            all_trades.extend(outcome["trades"])
            warnings.extend(outcome["warnings"])
            for day, equity in outcome["equity_curve"].items():
                total_equity[day] = total_equity.get(day, 0.0) + equity
        if not total_equity:
            total_equity = {"": spec.initial_capital}
        values = [total_equity[key] for key in sorted(total_equity)]
        metrics = _metrics(spec.initial_capital, values, all_trades)
        return {
            "scenario": scenario,
            "equity_curve": total_equity,
            "trades": all_trades,
            "metrics": metrics,
            "warnings": sorted(set(warnings)),
        }

    def _run_symbol(self, spec, code, bars, allocation, scenario):
        cash = allocation
        quantity = 0
        entry_price: Optional[float] = None
        entry_cost = 0.0
        entry_date: Optional[str] = None
        pending = None
        trades: List[Dict[str, Any]] = []
        warnings: List[str] = []
        equity_curve: Dict[str, float] = {}
        closes = [float(bar["close"]) for bar in bars]
        lot_size = _lot_size(spec)

        for index, bar in enumerate(bars):
            day = str(bar["date"])
            suspended = bool(bar.get("suspended") or bar.get("is_suspended"))

            if pending:
                pending_trade, filled, cash, quantity, entry_price, entry_cost, entry_date = (
                    self._execute_pending(
                        spec,
                        code,
                        bar,
                        pending,
                        cash,
                        quantity,
                        entry_price,
                        entry_cost,
                        entry_date,
                        allocation,
                        lot_size,
                    )
                )
                trades.append(pending_trade)
                if not filled:
                    warnings.append(pending_trade["reason"])
                pending = None

            if quantity > 0 and entry_date != day:
                dividend = _as_float(bar.get("cash_dividend"))
                if dividend and dividend > 0:
                    amount = quantity * dividend
                    cash += amount
                    warnings.append(
                        "{} {} 分红单独入账：{:.8f}".format(code, day, amount)
                    )

                if not suspended:
                    stop_take = _stop_take_trigger(spec, bar, entry_price, scenario)
                    if stop_take is not None:
                        reason, exit_price = stop_take
                        blocked = _blocked_reason(bar, "SELL", exit_price)
                        if blocked:
                            unfilled = _unfilled_trade(
                                code,
                                "SELL",
                                day,
                                day,
                                exit_price,
                                quantity,
                                "{}；止盈止损无法成交".format(blocked),
                            )
                            trades.append(unfilled)
                            warnings.append(unfilled["reason"])
                        else:
                            sell_trade, cash = _close_position(
                                spec,
                                code,
                                day,
                                day,
                                exit_price,
                                quantity,
                                entry_cost,
                                reason,
                                cash,
                            )
                            trades.append(sell_trade)
                            quantity = 0
                            entry_price = None
                            entry_cost = 0.0
                            entry_date = None

            if suspended:
                warnings.append("{} {} 停牌，无法产生或执行交易".format(code, day))
            elif quantity == 0 and _rules_match(spec.entry, index, closes):
                pending = {"side": "BUY", "signal_date": day, "reason": "ENTRY_RULE"}
            elif quantity > 0 and _rules_match(spec.exit, index, closes):
                pending = {"side": "SELL", "signal_date": day, "reason": "EXIT_RULE"}
            equity_curve[day] = cash + quantity * float(bar["close"])

        if quantity > 0:
            warnings.append("{} 数据结束时仍有未平仓头寸，按最后收盘价估值".format(code))
        return {"trades": trades, "equity_curve": equity_curve, "warnings": warnings}

    def _execute_pending(
        self,
        spec,
        code,
        bar,
        pending,
        cash,
        quantity,
        entry_price,
        entry_cost,
        entry_date,
        allocation,
        lot_size,
    ):
        day = str(bar["date"])
        raw_price = float(bar["open"])
        blocked = _blocked_reason(bar, pending["side"], raw_price)
        if blocked:
            return (
                _unfilled_trade(
                    code,
                    pending["side"],
                    day,
                    pending["signal_date"],
                    raw_price,
                    quantity if pending["side"] == "SELL" else 0,
                    "{}；订单未成交".format(blocked),
                ),
                False,
                cash,
                quantity,
                entry_price,
                entry_cost,
                entry_date,
            )

        rates = _cost_profile(spec)
        if pending["side"] == "BUY" and quantity == 0:
            fill_price = raw_price * (1 + rates["slippage_rate"])
            target_cash = _target_cash(spec, allocation)
            estimated_fee = _fee(rates, fill_price, 1, target_cash)
            affordable = max(0.0, target_cash - estimated_fee)
            target_quantity = floor(affordable / fill_price / lot_size) * lot_size
            if target_quantity <= 0:
                trade = _unfilled_trade(
                    code,
                    "BUY",
                    day,
                    pending["signal_date"],
                    raw_price,
                    0,
                    "资金不足或不足一个交易单位，订单未成交",
                )
                return trade, False, cash, quantity, entry_price, entry_cost, entry_date
            gross = target_quantity * fill_price
            fee = _fee(rates, fill_price, target_quantity, gross)
            cash -= gross + fee
            trade = {
                "code": code,
                "side": "BUY",
                "date": day,
                "signal_date": pending["signal_date"],
                "price": round(fill_price, 8),
                "raw_price": raw_price,
                "quantity": target_quantity,
                "fee": round(fee, 8),
                "reason": pending["reason"],
            }
            return (
                trade,
                True,
                cash,
                target_quantity,
                fill_price,
                gross + fee,
                day,
            )

        if pending["side"] == "SELL" and quantity > 0:
            fill_price = raw_price * (1 - rates["slippage_rate"])
            trade, cash = _close_position(
                spec,
                code,
                day,
                pending["signal_date"],
                fill_price,
                quantity,
                entry_cost,
                pending["reason"],
                cash,
            )
            return trade, True, cash, 0, None, 0.0, None

        return (
            _unfilled_trade(
                code,
                pending["side"],
                day,
                pending["signal_date"],
                raw_price,
                quantity,
                "当前持仓状态与订单不匹配，订单未成交",
            ),
            False,
            cash,
            quantity,
            entry_price,
            entry_cost,
            entry_date,
        )

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
            "train_end": dates[split_index - 1] if train_dates else None,
            "test_start": dates[split_index] if test_dates else None,
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


def _metrics(initial: float, values: List[float], trades: List[Dict[str, Any]]):
    final = values[-1] if values else initial
    peak = initial
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    completed = [trade for trade in trades if trade.get("side") == "SELL" and trade.get("status") != "UNFILLED"]
    wins = [trade for trade in completed if trade.get("pnl", 0) > 0]
    return {
        "initial_capital": initial,
        "final_equity": round(final, 8),
        "cumulative_return": round(final / initial - 1, 8),
        "max_drawdown": round(max_drawdown, 8),
        "trade_count": len(completed),
        "win_rate": round(len(wins) / len(completed), 8) if completed else None,
    }


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
    return {
        "source_name": policy.get("source_name", "a-stock-data"),
        "source_url": policy.get("source_url"),
        "source_version": policy.get("source_version", "a-stock-data:unknown"),
        "skill_name": policy.get("skill_name", "a-stock-data"),
        "skill_version": policy.get("skill_version"),
        "data_start": min(dates) if dates else None,
        "data_end": max(dates) if dates else None,
        "frequency": spec.frequency,
        "price_basis": policy.get("price_basis", "adjusted"),
        "corporate_actions": actions,
        "cost_profile": _cost_profile(spec, include_defaults=True),
    }


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
    commission = max(
        gross * float(rates.get("commission_rate", 0.0)),
        float(rates.get("minimum_commission", 0.0)) if gross > 0 else 0.0,
    )
    transfer = gross * float(rates.get("transfer_fee_rate", 0.0))
    stamp = gross * float(rates.get("stamp_duty_rate", 0.0)) if side == "SELL" else 0.0
    return commission + transfer + stamp


def _close_position(spec, code, day, signal_date, price, quantity, entry_cost, reason, cash):
    rates = _cost_profile(spec)
    gross = quantity * price
    fee = _fee(rates, price, quantity, gross, side="SELL")
    revenue = gross - fee
    pnl = revenue - entry_cost
    trade = {
        "code": code,
        "side": "SELL",
        "date": day,
        "signal_date": signal_date,
        "price": round(price, 8),
        "quantity": quantity,
        "fee": round(fee, 8),
        "pnl": round(pnl, 8),
        "reason": reason,
    }
    return trade, cash + revenue


def _unfilled_trade(code, side, day, signal_date, price, quantity, reason):
    return {
        "code": code,
        "side": side,
        "date": day,
        "signal_date": signal_date,
        "price": round(price, 8),
        "quantity": quantity,
        "status": "UNFILLED",
        "reason": reason,
    }


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


def _target_cash(spec, allocation):
    sizing = spec.position_sizing or {}
    kind = sizing.get("type", "all_in")
    if kind == "fixed_cash":
        return min(allocation, float(sizing.get("cash", allocation)))
    if kind == "fixed_fraction":
        return allocation * float(sizing.get("fraction", 1.0))
    return allocation


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
