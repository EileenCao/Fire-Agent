"""Deterministic performance and trade statistics for report generation."""

from datetime import date
from math import isfinite, sqrt
from statistics import stdev
from typing import Any, Dict, Iterable, List, Optional


TRADING_DAYS_PER_YEAR = 252


def calculate_benchmark_metrics(
    strategy_equity: Dict[str, float],
    benchmark_equity: Dict[str, float],
    risk_free_rate_annual: Optional[float] = 0.0,
) -> Dict[str, Any]:
    aligned = sorted(set(strategy_equity) & set(benchmark_equity))
    coverage = len(aligned) / len(strategy_equity) if strategy_equity else 0.0
    if len(aligned) < 2:
        return {
            "status": "unavailable",
            "reason": "策略与基准没有足够重叠日期",
            "coverage": round(coverage, 8),
            "aligned_start": aligned[0] if aligned else None,
            "aligned_end": aligned[-1] if aligned else None,
        }

    strategy_values = [float(strategy_equity[day]) for day in aligned]
    benchmark_values = [float(benchmark_equity[day]) for day in aligned]
    strategy_returns = _period_daily_returns(strategy_values)
    benchmark_returns = _period_daily_returns(benchmark_values)
    annual_rf = float(risk_free_rate_annual or 0.0)
    daily_rf = (1.0 + annual_rf) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0
    strategy_excess = [value - daily_rf for value in strategy_returns]
    benchmark_excess = [value - daily_rf for value in benchmark_returns]
    active = [s - b for s, b in zip(strategy_returns, benchmark_returns)]
    tracking_error = _annualized_stdev(active)
    benchmark_variance = _variance(benchmark_excess)
    beta = _ratio(_covariance(strategy_excess, benchmark_excess), benchmark_variance)
    alpha = (
        (_mean(strategy_excess) - beta * _mean(benchmark_excess)) * TRADING_DAYS_PER_YEAR
        if beta is not None
        else None
    )
    return {
        "status": "ok",
        "coverage": round(coverage, 8),
        "aligned_start": aligned[0],
        "aligned_end": aligned[-1],
        "strategy_return": round(strategy_values[-1] / strategy_values[0] - 1.0, 8),
        "benchmark_return": round(benchmark_values[-1] / benchmark_values[0] - 1.0, 8),
        "excess_return": round(
            strategy_values[-1] / strategy_values[0]
            - benchmark_values[-1] / benchmark_values[0],
            8,
        ),
        "tracking_error": _round_or_none(tracking_error),
        "information_ratio": _round_or_none(
            _ratio(_mean(active) * TRADING_DAYS_PER_YEAR, tracking_error)
        ),
        "beta": _round_or_none(beta),
        "alpha": _round_or_none(alpha),
    }


def calculate_performance_metrics(
    initial: float,
    equity_curve: Dict[str, float],
    trades: Iterable[Dict[str, Any]],
    cash_flows: Optional[Iterable[Dict[str, Any]]] = None,
    risk_free_rate_annual: Optional[float] = 0.0,
    cash_curve: Optional[Dict[str, float]] = None,
    market_value_curve: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    dates = sorted(str(day) for day in equity_curve if str(day))
    values = [float(equity_curve[day]) for day in dates]
    trades = [dict(trade) for trade in trades]
    cash_flows = [dict(flow) for flow in (cash_flows or [])]
    if not values:
        return _empty_metrics(float(initial))

    flow_by_day: Dict[str, float] = {}
    for flow in cash_flows:
        day = str(flow.get("date", ""))
        flow_by_day[day] = flow_by_day.get(day, 0.0) + float(flow.get("amount", 0.0))

    daily_returns = []
    time_weighted_factor = 1.0
    previous = float(initial)
    for day, current in zip(dates, values):
        base = previous + flow_by_day.get(day, 0.0)
        if base > 0:
            factor = current / base
            time_weighted_factor *= factor
            daily_returns.append(factor - 1.0)
        previous = current

    external = sum(float(flow.get("amount", 0.0)) for flow in cash_flows)
    final = values[-1]
    elapsed_days = max(1, (_parse_date(dates[-1]) - _parse_date(dates[0])).days)
    time_weighted_return = time_weighted_factor - 1.0
    annualized_return = _annualize(time_weighted_factor, elapsed_days)
    annual_rf = float(risk_free_rate_annual or 0.0)
    daily_rf = (1.0 + annual_rf) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0
    excess = [value - daily_rf for value in daily_returns]
    volatility = _annualized_stdev(daily_returns)
    downside = _annualized_downside_deviation(excess)
    sharpe = _ratio(_mean(excess) * TRADING_DAYS_PER_YEAR, volatility)
    sortino = _ratio(_mean(excess) * TRADING_DAYS_PER_YEAR, downside)

    drawdown = _drawdown_stats(dates, values)
    max_drawdown = drawdown["max_drawdown"]
    calmar = _ratio(annualized_return, max_drawdown)
    completed = [
        trade
        for trade in trades
        if trade.get("side") == "SELL" and trade.get("status") != "UNFILLED"
    ]
    wins = [float(trade.get("pnl", 0.0)) for trade in completed if float(trade.get("pnl", 0.0)) > 0]
    losses = [float(trade.get("pnl", 0.0)) for trade in completed if float(trade.get("pnl", 0.0)) < 0]
    realized = wins + losses
    gross_notional = sum(
        abs(float(trade.get("price", 0.0)) * float(trade.get("quantity", 0.0)))
        for trade in trades
        if trade.get("status") != "UNFILLED"
    )
    total_fees = sum(float(trade.get("fee", 0.0) or 0.0) for trade in trades)
    commission = _trade_amount(trades, "commission")
    stamp_duty = _trade_amount(trades, "stamp_duty")
    transfer_fee = _trade_amount(trades, "transfer_fee")
    slippage_impact = _trade_amount(trades, "slippage_impact")
    average_equity = _mean(values)
    holding_days = [
        float(trade["holding_days"])
        for trade in completed
        if trade.get("holding_days") is not None
    ]
    curve_metrics = _curve_metrics(
        dates,
        values,
        cash_curve=cash_curve,
        market_value_curve=market_value_curve,
    )
    cash_neutral_metrics = _cash_neutral_metrics(
        market_value_curve=market_value_curve,
        trades=trades,
    )
    return {
        "initial_capital": float(initial),
        "final_equity": round(final, 8),
        "external_cash_flow": round(external, 8),
        "total_contributed": round(initial + external, 8),
        "net_profit": round(final - initial - external, 8),
        "cumulative_return": round(final / initial - 1.0, 8) if not external else None,
        "time_weighted_return": round(time_weighted_return, 8),
        "annualized_return": _round_or_none(annualized_return),
        "annualized_volatility": _round_or_none(volatility),
        "downside_volatility": _round_or_none(downside),
        "risk_free_rate_annual": annual_rf,
        "sharpe_ratio": _round_or_none(sharpe),
        "sortino_ratio": _round_or_none(sortino),
        "calmar_ratio": _round_or_none(calmar),
        **drawdown,
        "trade_count": len(completed),
        "realized_sell_count": len(completed),
        "filled_order_count": sum(1 for trade in trades if trade.get("status") != "UNFILLED"),
        "unfilled_order_count": sum(1 for trade in trades if trade.get("status") == "UNFILLED"),
        "buy_fill_count": sum(1 for trade in trades if trade.get("side") == "BUY" and trade.get("status") != "UNFILLED"),
        "sell_fill_count": sum(1 for trade in trades if trade.get("side") == "SELL" and trade.get("status") != "UNFILLED"),
        "fill_rate": _ratio(
            sum(1 for trade in trades if trade.get("status") != "UNFILLED"), len(trades)
        ),
        "win_rate": _ratio(len(wins), len(completed)),
        "profit_factor": _ratio(sum(wins), abs(sum(losses))),
        "payoff_ratio": _ratio(_mean(wins), _abs_or_none(_mean(losses))),
        "expectancy": _mean(realized),
        "average_win": _mean(wins),
        "average_loss": _mean(losses),
        "largest_win": max(wins) if wins else None,
        "largest_loss": min(losses) if losses else None,
        "max_consecutive_wins": _max_consecutive([value > 0 for value in realized]),
        "max_consecutive_losses": _max_consecutive([value < 0 for value in realized]),
        "average_holding_days": _mean(holding_days),
        "gross_notional": round(gross_notional, 8),
        "total_fees": round(total_fees, 8),
        "commission": round(commission, 8),
        "stamp_duty": round(stamp_duty, 8),
        "transfer_fee": round(transfer_fee, 8),
        "slippage_impact": round(slippage_impact, 8),
        "turnover": _ratio(gross_notional, average_equity),
        "annual_returns": _period_returns(dates, values, "%Y"),
        "monthly_returns": _period_returns(dates, values, "%Y-%m"),
        **curve_metrics,
        **cash_neutral_metrics,
    }


def _empty_metrics(initial: float) -> Dict[str, Any]:
    return {
        "initial_capital": initial,
        "final_equity": initial,
        "net_profit": 0.0,
        "cumulative_return": 0.0,
        "time_weighted_return": 0.0,
        "annualized_return": None,
        "annualized_volatility": None,
        "downside_volatility": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "max_drawdown": None,
        "trade_count": 0,
        "realized_sell_count": 0,
        "filled_order_count": 0,
        "unfilled_order_count": 0,
        "win_rate": None,
        "profit_factor": None,
        "payoff_ratio": None,
        "expectancy": None,
        "annual_returns": {},
        "monthly_returns": {},
        "commission": 0.0,
        "stamp_duty": 0.0,
        "transfer_fee": 0.0,
        "slippage_impact": 0.0,
        "average_exposure": None,
        "max_exposure": None,
        "time_in_market_ratio": None,
        "current_cash": None,
        "current_market_value": None,
        "cash_neutral_cumulative_return": None,
        "cash_neutral_annualized_return": None,
        "cash_neutral_active_sessions": 0,
        "cash_neutral_twr_cumulative_return": None,
        "cash_neutral_twr_annualized_return": None,
        "cash_neutral_active_calendar_days": 0,
        "cash_neutral_max_drawdown": None,
        "cash_neutral_max_drawdown_peak_date": None,
        "cash_neutral_max_drawdown_trough_date": None,
        "cash_neutral_max_drawdown_recovery_date": None,
        "cash_neutral_max_drawdown_duration_days": 0,
    }


def _drawdown_stats(dates: List[str], values: List[float]) -> Dict[str, Any]:
    max_drawdown = 0.0
    peak_index = 0
    max_peak_index = max_trough_index = 0
    drawdown_periods = []
    current_peak_index = 0
    running_peak = float(values[0])
    for index, value in enumerate(values):
        value = float(value)
        if value >= running_peak:
            if index > current_peak_index:
                drawdown_periods.append((current_peak_index, index - 1))
            running_peak = value
            current_peak_index = index
        drawdown = (running_peak - value) / running_peak if running_peak else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_peak_index = current_peak_index
            max_trough_index = index
    if current_peak_index < len(values) - 1:
        drawdown_periods.append((current_peak_index, len(values) - 1))

    recovery_index = None
    if max_drawdown > 0:
        peak_value = float(values[max_peak_index])
        for index in range(max_trough_index + 1, len(values)):
            if float(values[index]) >= peak_value:
                recovery_index = index
                break
    longest_duration = max(
        (end - start + 1 for start, end in drawdown_periods if end > start),
        default=0,
    )
    max_duration_end = recovery_index if recovery_index is not None else len(values) - 1
    max_duration_days = (
        _parse_date(dates[max_duration_end]) - _parse_date(dates[max_peak_index])
    ).days if max_drawdown > 0 else 0
    return {
        "max_drawdown": round(max_drawdown, 8),
        "max_drawdown_peak_date": dates[max_peak_index] if max_drawdown > 0 else dates[0],
        "max_drawdown_trough_date": dates[max_trough_index] if max_drawdown > 0 else dates[0],
        "max_drawdown_recovery_date": dates[recovery_index] if recovery_index is not None else None,
        "max_drawdown_duration_days": max_duration_days,
        "max_drawdown_duration_sessions": longest_duration,
    }


def _curve_metrics(
    dates: List[str],
    values: List[float],
    cash_curve: Optional[Dict[str, float]],
    market_value_curve: Optional[Dict[str, float]],
) -> Dict[str, Any]:
    if not market_value_curve:
        return {
            "average_exposure": None,
            "max_exposure": None,
            "time_in_market_ratio": None,
            "current_cash": None,
            "current_market_value": None,
        }
    exposures = []
    for day, equity in zip(dates, values):
        market_value = float(market_value_curve.get(day, 0.0) or 0.0)
        exposures.append(market_value / equity if equity else 0.0)
    return {
        "average_exposure": _round_or_none(_mean(exposures)),
        "max_exposure": _round_or_none(max(exposures) if exposures else None),
        "time_in_market_ratio": _ratio(
            sum(1 for value in exposures if value > 0), len(exposures)
        ),
        "current_cash": float((cash_curve or {}).get(dates[-1], 0.0)),
        "current_market_value": float(market_value_curve.get(dates[-1], 0.0)),
    }


def _cash_neutral_metrics(
    market_value_curve: Optional[Dict[str, float]],
    trades: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Measure invested-capital return while excluding idle cash.

    Buys are treated as invested-capital outflows, sells as capital returns,
    and the final market value as the terminal return. This is an XIRR-style
    money-weighted return for the invested sub-account; cash left outside the
    sub-account never enters its denominator.
    """
    trades = [dict(trade) for trade in trades]
    twr_metrics = _cash_neutral_twr_metrics(market_value_curve, trades)
    if not market_value_curve:
        return {
            "cash_neutral_cumulative_return": None,
            "cash_neutral_annualized_return": None,
            "cash_neutral_active_sessions": 0,
            **twr_metrics,
        }

    dates = sorted(str(day) for day in market_value_curve if str(day))
    if not dates:
        return {
            "cash_neutral_cumulative_return": None,
            "cash_neutral_annualized_return": None,
            "cash_neutral_active_sessions": 0,
            **twr_metrics,
        }

    cash_flows = []
    for raw_trade in trades:
        trade = dict(raw_trade)
        if trade.get("status") not in {"FILLED", "PARTIAL"}:
            continue
        day = str(trade.get("date", ""))
        if not day or trade.get("side") not in {"BUY", "SELL"}:
            continue
        notional = float(trade.get("price", 0.0)) * float(
            trade.get("filled_quantity", trade.get("quantity", 0.0)) or 0.0
        )
        fee = float(trade.get("fee", 0.0) or 0.0)
        amount = -(notional + fee) if trade["side"] == "BUY" else notional - fee
        cash_flows.append((_parse_date(day), amount))

    if not cash_flows:
        return {
            "cash_neutral_cumulative_return": None,
            "cash_neutral_annualized_return": None,
            "cash_neutral_active_sessions": 0,
            **twr_metrics,
        }

    final_day = _parse_date(dates[-1])
    cash_flows.append((final_day, float(market_value_curve[dates[-1]] or 0.0)))
    cash_flows.sort(key=lambda item: item[0])
    rate = _xirr(cash_flows)
    active_sessions = sum(
        1
        for previous, current in zip(dates, dates[1:])
        if float(market_value_curve[previous] or 0.0) > 0.0
    )
    if rate is None:
        return {
            "cash_neutral_cumulative_return": None,
            "cash_neutral_annualized_return": None,
            "cash_neutral_active_sessions": active_sessions,
            **twr_metrics,
        }
    elapsed_days = max(1, (final_day - cash_flows[0][0]).days)
    factor = (1.0 + rate) ** (elapsed_days / 365.25)
    return {
        "cash_neutral_cumulative_return": round(factor - 1.0, 8),
        "cash_neutral_annualized_return": _round_or_none(rate),
        "cash_neutral_active_sessions": active_sessions,
        **twr_metrics,
    }


def _cash_neutral_twr_metrics(
    market_value_curve: Optional[Dict[str, float]],
    trades: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calculate active-position TWR and drawdown without idle cash."""

    empty = {
        "cash_neutral_twr_cumulative_return": None,
        "cash_neutral_twr_annualized_return": None,
        "cash_neutral_active_calendar_days": 0,
        "cash_neutral_max_drawdown": None,
        "cash_neutral_max_drawdown_peak_date": None,
        "cash_neutral_max_drawdown_trough_date": None,
        "cash_neutral_max_drawdown_recovery_date": None,
        "cash_neutral_max_drawdown_duration_days": 0,
    }
    if not market_value_curve:
        return empty

    dates = sorted(str(day) for day in market_value_curve if str(day))
    if not dates:
        return empty

    flows_by_day: Dict[str, List[float]] = {}
    for raw_trade in trades:
        trade = dict(raw_trade)
        if trade.get("status") not in {"FILLED", "PARTIAL"}:
            continue
        day = str(trade.get("date", ""))
        if not day or trade.get("side") not in {"BUY", "SELL"}:
            continue
        notional = float(trade.get("price", 0.0)) * float(
            trade.get("filled_quantity", trade.get("quantity", 0.0)) or 0.0
        )
        values = flows_by_day.setdefault(day, [0.0, 0.0])
        if trade["side"] == "BUY":
            values[0] += notional
        else:
            values[1] += notional

    factor = 1.0
    active_sessions = 0
    episodes = []
    episode = None
    previous_day = None
    previous_value = 0.0
    for day in dates:
        current_value = float(market_value_curve.get(day, 0.0) or 0.0)
        buy_value, sell_value = flows_by_day.get(day, [0.0, 0.0])

        if previous_day is not None and previous_value > 0.0:
            pre_flow_value = current_value + sell_value - buy_value
            daily_factor = pre_flow_value / previous_value
            factor *= daily_factor
            active_sessions += 1
            if episode is None:
                episode = {"dates": [previous_day], "values": [1.0]}
            episode["dates"].append(day)
            episode["values"].append(episode["values"][-1] * daily_factor)

        if previous_value <= 0.0 and current_value > 0.0:
            episode = {"dates": [day], "values": [1.0]}

        if previous_value > 0.0 and current_value <= 0.0:
            if episode is not None:
                episodes.append(episode)
                episode = None

        previous_day = day
        previous_value = current_value

    if episode is not None:
        episodes.append(episode)
    if not episodes:
        return empty

    active_calendar_days = sum(
        max(1, (_parse_date(item["dates"][-1]) - _parse_date(item["dates"][0])).days + 1)
        for item in episodes
    )
    drawdown_stats = [
        _drawdown_stats(item["dates"], item["values"]) for item in episodes
    ]
    worst = max(drawdown_stats, key=lambda item: item["max_drawdown"])
    return {
        "cash_neutral_twr_cumulative_return": round(factor - 1.0, 8),
        "cash_neutral_twr_annualized_return": _round_or_none(
            _annualize(factor, active_calendar_days)
        ),
        "cash_neutral_active_calendar_days": active_calendar_days,
        "cash_neutral_max_drawdown": worst["max_drawdown"],
        "cash_neutral_max_drawdown_peak_date": (
            worst["max_drawdown_peak_date"]
            if worst["max_drawdown"] > 0
            else None
        ),
        "cash_neutral_max_drawdown_trough_date": (
            worst["max_drawdown_trough_date"]
            if worst["max_drawdown"] > 0
            else None
        ),
        "cash_neutral_max_drawdown_recovery_date": (
            worst["max_drawdown_recovery_date"]
            if worst["max_drawdown"] > 0
            else None
        ),
        "cash_neutral_max_drawdown_duration_days": (
            worst["max_drawdown_duration_days"]
            if worst["max_drawdown"] > 0
            else 0
        ),
    }


def _xirr(cash_flows) -> Optional[float]:
    if len(cash_flows) < 2:
        return None
    if not any(amount < 0 for _, amount in cash_flows):
        return None
    if not any(amount > 0 for _, amount in cash_flows):
        return None

    start = cash_flows[0][0]

    def npv(rate):
        return sum(
            amount
            / ((1.0 + rate) ** ((day - start).days / 365.25))
            for day, amount in cash_flows
        )

    low = -0.9999
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)
    for _ in range(256):
        if low_value * high_value <= 0.0:
            break
        high = high * 2.0 + 1.0
        high_value = npv(high)
    if low_value * high_value > 0.0:
        return None
    for _ in range(200):
        middle = (low + high) / 2.0
        middle_value = npv(middle)
        if abs(middle_value) < 1e-8:
            return middle
        if low_value * middle_value <= 0.0:
            high, high_value = middle, middle_value
        else:
            low, low_value = middle, middle_value
    return (low + high) / 2.0


def _trade_amount(trades: Iterable[Dict[str, Any]], key: str) -> float:
    return sum(float(trade.get(key, 0.0) or 0.0) for trade in trades)


def _period_returns(dates: List[str], values: List[float], fmt: str) -> Dict[str, float]:
    result = {}
    current_period = None
    period_first = None
    period_last = None
    previous_period_end = None
    for day, value in zip(dates, values):
        period = _parse_date(day).strftime(fmt)
        if current_period is None:
            current_period = period
            period_first = value
        elif period != current_period:
            base = previous_period_end if previous_period_end is not None else period_first
            result[current_period] = round(period_last / base - 1.0, 8) if base else None
            previous_period_end = period_last
            current_period = period
            period_first = value
        period_last = value
    if current_period is not None and period_first:
        base = previous_period_end if previous_period_end is not None else period_first
        result[current_period] = round(period_last / base - 1.0, 8) if base else None
    return result


def _annualize(factor: float, elapsed_days: int) -> Optional[float]:
    if factor <= 0:
        return None
    return factor ** (365.25 / max(1, elapsed_days)) - 1.0


def _annualized_stdev(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    return stdev(values) * sqrt(TRADING_DAYS_PER_YEAR)


def _annualized_downside_deviation(values: List[float]) -> Optional[float]:
    if not values:
        return None
    negative = [min(0.0, value) ** 2 for value in values]
    return sqrt(sum(negative) / len(negative)) * sqrt(TRADING_DAYS_PER_YEAR)


def _period_daily_returns(values: List[float]) -> List[float]:
    return [current / previous - 1.0 for previous, current in zip(values, values[1:]) if previous]


def _variance(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    average = _mean(values)
    return sum((value - average) ** 2 for value in values) / (len(values) - 1)


def _covariance(left: List[float], right: List[float]) -> Optional[float]:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = _mean(left)
    right_mean = _mean(right)
    return sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    ) / (len(left) - 1)


def _max_consecutive(values: List[bool]) -> int:
    current = longest = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def _mean(values: Iterable[float]) -> Optional[float]:
    values = list(values)
    return sum(values) / len(values) if values else None


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _round_or_none(value: Optional[float]) -> Optional[float]:
    return round(value, 8) if value is not None and isfinite(value) else None


def _abs_or_none(value: Optional[float]) -> Optional[float]:
    return abs(value) if value is not None else None


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])
