"""Versioned strategy contracts shared by the Agent, MCP and backtest engine."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from mcp_server.domain.identifiers import normalize_ticker


_ACTION_ALIASES = {"EXIT": "SELL", "ENTRY": "SIGNAL_BUY"}
_VALID_ACTIONS = {"SELL", "PERIODIC_BUY", "SIGNAL_BUY"}
_VALID_BUY_TYPES = {
    "all_in",
    "fixed_cash",
    "fixed_quantity",
    "cash_pct",
    "fixed_fraction",
    "recurrent_cash",
}


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    version: str
    name: str
    universe: List[str]
    frequency: str
    entry: Dict[str, Any]
    exit: Dict[str, Any]
    position_sizing: Optional[Dict[str, Any]]
    initial_capital: float = 100000.0
    benchmark: Optional[Dict[str, Any]] = None
    risk_free_rate_annual: Optional[float] = None
    cost_profile: Dict[str, Any] = field(default_factory=dict)
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    data_policy: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    action_priority: List[str] = field(default_factory=lambda: ["EXIT", "ENTRY"])
    indicators: List[Dict[str, Any]] = field(default_factory=list)
    execution: Dict[str, Any] = field(default_factory=dict)
    plugin: Optional[Dict[str, Any]] = None
    validation_errors: List[str] = field(default_factory=list, compare=False)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StrategySpec":
        errors = []
        required = ("strategy_id", "version", "name", "universe", "entry", "exit")
        for key in required:
            if not payload.get(key):
                errors.append("缺少策略字段：{}".format(key))
        position_sizing = payload.get("position_sizing")
        if not position_sizing:
            errors.append("正式回测必须明确仓位方案")
        elif not isinstance(position_sizing, dict):
            errors.append("仓位方案必须是对象")
            position_sizing = {}
        else:
            position_sizing = dict(position_sizing)
            position_sizing.setdefault("capital_scope", "per_symbol")
        universe = []
        for raw_code in payload.get("universe") or []:
            value = str(raw_code)
            try:
                value, _ = normalize_ticker(value)
            except ValueError:
                # Keep non-market fixture identifiers usable in unit tests.
                pass
            universe.append(value)
        if not universe:
            errors.append("策略标的范围不能为空")
        frequency = str(payload.get("frequency", "1d"))
        if frequency != "1d":
            errors.append("首版只支持日线频率 1d")
        initial_capital = payload.get("initial_capital", 100000.0)
        try:
            initial_capital = float(initial_capital)
            if initial_capital <= 0:
                errors.append("初始资金必须大于 0")
        except (TypeError, ValueError):
            errors.append("初始资金必须是数字")
            initial_capital = 100000.0
        risk_free_rate_annual = payload.get("risk_free_rate_annual")
        if risk_free_rate_annual is not None:
            try:
                risk_free_rate_annual = float(risk_free_rate_annual)
                if risk_free_rate_annual <= -1 or risk_free_rate_annual > 1:
                    errors.append("risk_free_rate_annual 必须大于 -1 且不超过 1")
            except (TypeError, ValueError):
                errors.append("risk_free_rate_annual 必须是数字")
                risk_free_rate_annual = None
        benchmark = payload.get("benchmark")
        if benchmark is not None and not isinstance(benchmark, dict):
            errors.append("benchmark 必须是对象或 null")
            benchmark = None
        elif isinstance(benchmark, dict):
            for field_name in ("code", "market", "instrument_type", "name"):
                if not benchmark.get(field_name):
                    errors.append("benchmark 缺少字段：{}".format(field_name))
        for field_name in ("stop_loss_pct", "take_profit_pct"):
            value = payload.get(field_name)
            if value is not None:
                try:
                    if float(value) <= 0 or float(value) >= 1:
                        errors.append("{} 必须在 0 和 1 之间".format(field_name))
                except (TypeError, ValueError):
                    errors.append("{} 必须是数字".format(field_name))
        plugin = payload.get("plugin")
        if plugin and not plugin.get("approved"):
            errors.append("Python 策略插件必须先审阅并批准")
        errors.extend(_validate_position_sizing(position_sizing))
        errors.extend(_validate_exit(payload.get("exit") or {}, position_sizing))
        indicators = [dict(item) for item in payload.get("indicators") or []]
        errors.extend(_validate_rsi_contract(payload, indicators, position_sizing))
        execution = dict(payload.get("execution") or {})
        errors.extend(_validate_execution(execution))
        action_priority, priority_errors = _normalize_action_priority(
            payload.get("action_priority")
        )
        errors.extend(priority_errors)
        return cls(
            strategy_id=str(payload.get("strategy_id", "")),
            version=str(payload.get("version", "")),
            name=str(payload.get("name", "")),
            universe=universe,
            frequency=frequency,
            entry=dict(payload.get("entry") or {}),
            exit=dict(payload.get("exit") or {}),
            position_sizing=dict(position_sizing) if position_sizing else None,
            initial_capital=initial_capital,
            benchmark=dict(benchmark) if benchmark else None,
            risk_free_rate_annual=risk_free_rate_annual,
            cost_profile=dict(payload.get("cost_profile") or {}),
            stop_loss_pct=payload.get("stop_loss_pct"),
            take_profit_pct=payload.get("take_profit_pct"),
            data_policy=dict(payload.get("data_policy") or {}),
            validation=dict(payload.get("validation") or {}),
            action_priority=action_priority,
            indicators=indicators,
            execution=execution,
            plugin=dict(payload["plugin"]) if payload.get("plugin") else None,
            validation_errors=errors,
        )

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "name": self.name,
            "universe": list(self.universe),
            "frequency": self.frequency,
            "entry": dict(self.entry),
            "exit": dict(self.exit),
            "position_sizing": dict(self.position_sizing) if self.position_sizing else None,
            "initial_capital": self.initial_capital,
            "benchmark": dict(self.benchmark) if self.benchmark else None,
            "risk_free_rate_annual": self.risk_free_rate_annual,
            "cost_profile": dict(self.cost_profile),
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "data_policy": dict(self.data_policy),
            "validation": dict(self.validation),
            "action_priority": list(self.action_priority),
            "indicators": [dict(item) for item in self.indicators],
            "execution": dict(self.execution),
            "plugin": dict(self.plugin) if self.plugin else None,
        }


def _normalize_action_priority(value):
    raw = value or ["EXIT", "ENTRY"]
    if not isinstance(raw, list) or not raw:
        return ["SELL", "SIGNAL_BUY"], ["action_priority 必须是非空数组"]
    normalized = []
    errors = []
    for item in raw:
        action = _ACTION_ALIASES.get(str(item).upper(), str(item).upper())
        if action not in _VALID_ACTIONS:
            errors.append("不支持的订单优先级：{}".format(item))
            continue
        if action in normalized:
            errors.append("订单优先级不能重复：{}".format(action))
            continue
        normalized.append(action)
    return normalized or ["SELL", "SIGNAL_BUY"], errors


def validate_run_assumptions(
    payload: Dict[str, Any],
    confirm_benchmark: bool,
    confirm_risk_free_rate: bool,
) -> List[str]:
    """Validate assumptions that must be explicitly confirmed before a run."""
    errors = []
    if "benchmark" not in payload:
        errors.append("策略必须显式选择 benchmark（对象或 null）")
    elif payload.get("benchmark") is not None:
        benchmark = payload.get("benchmark")
        if not isinstance(benchmark, dict):
            errors.append("benchmark 必须是对象或 null")
        else:
            for field_name in ("code", "market", "instrument_type", "name"):
                if not benchmark.get(field_name):
                    errors.append("benchmark 缺少字段：{}".format(field_name))
    if "risk_free_rate_annual" not in payload:
        errors.append("策略必须显式设置 risk_free_rate_annual")
    elif payload.get("risk_free_rate_annual") is None:
        errors.append("risk_free_rate_annual 不能为 null")
    else:
        try:
            value = float(payload.get("risk_free_rate_annual"))
            if value <= -1 or value > 1:
                errors.append("risk_free_rate_annual 必须大于 -1 且不超过 1")
        except (TypeError, ValueError):
            errors.append("risk_free_rate_annual 必须是数字")
    if not confirm_benchmark:
        errors.append("回测前必须确认 benchmark 选择")
    if not confirm_risk_free_rate:
        errors.append("回测前必须确认年化无风险利率")
    return errors


def _validate_position_sizing(sizing):
    if not sizing:
        return []
    errors = []
    exit_mode = sizing.get("exit_mode")
    if exit_mode is not None and exit_mode not in {"rsi", "recovery"}:
        errors.append("exit_mode must be 'rsi' or 'recovery'")
    sell_basis = sizing.get("sell_basis")
    if sell_basis is not None and sell_basis not in {
        "all_tactical",
        "profitable_tactical",
    }:
        errors.append("sell_basis must be 'all_tactical' or 'profitable_tactical'")
    scope = sizing.get("capital_scope", "per_symbol")
    if scope not in {"per_symbol", "portfolio"}:
        errors.append("capital_scope 必须是 per_symbol 或 portfolio")
    kind = str(sizing.get("type", "all_in"))
    if kind not in _VALID_BUY_TYPES:
        errors.append("不支持的仓位类型：{}".format(kind))
    lot_size = _positive_int(sizing.get("lot_size", 100))
    if lot_size is None:
        errors.append("交易单位 lot_size 必须是正整数")
        lot_size = 100
    if kind == "fixed_cash" and _positive_number(sizing.get("cash", sizing.get("amount"))) is None:
        errors.append("fixed_cash 必须配置正数 cash 或 amount")
    if kind == "fixed_quantity" and _positive_int(sizing.get("quantity")) is None:
        errors.append("fixed_quantity 必须配置正整数 quantity")
    if kind == "recurrent_cash":
        initial_quantity = sizing.get("initial_quantity", 0)
        if _nonnegative_int(initial_quantity) is None:
            errors.append("recurrent_cash initial_quantity 必须是非负整数")
    errors.extend(_validate_layered_sizing(sizing))
    if kind in {"cash_pct", "fixed_fraction"}:
        fraction = sizing.get("fraction", sizing.get("amount"))
        if _positive_number(fraction) is None or float(fraction) > 1:
            errors.append("现金比例必须大于 0 且不超过 1")

    while_holding = sizing.get("while_holding") or {}
    if not isinstance(while_holding, dict):
        return errors + ["while_holding 必须是对象"]
    for key in ("signal_add", "periodic"):
        config = while_holding.get(key)
        if config is None or not isinstance(config, dict) or not config.get("enabled", True):
            continue
        errors.extend(_validate_buy_plan(config, key, lot_size))
    return errors


def _validate_layered_sizing(sizing):
    errors = []
    core = sizing.get("core")
    if core is not None:
        if not isinstance(core, dict):
            errors.append("core 必须是对象")
        else:
            ratio = _number(core.get("ratio"))
            if ratio is None or ratio < 0 or ratio >= 1:
                errors.append("core ratio 必须大于等于 0 且小于 1")
            if core.get("trigger", "first_entry_signal") != "first_entry_signal":
                errors.append("core trigger 必须是 first_entry_signal")
            if core.get("hold", True) is not True:
                errors.append("core hold 必须为 true")

    ladder = sizing.get("drawdown_ladder")
    fibonacci = sizing.get("fibonacci_ladder")
    if ladder is not None and fibonacci is not None:
        errors.append("drawdown_ladder 和 fibonacci_ladder 不能同时配置")
    if ladder is None:
        return errors + _validate_fibonacci_ladder(fibonacci)
    if not isinstance(ladder, dict):
        return errors + ["drawdown_ladder 必须是对象"]

    thresholds = ladder.get("thresholds")
    amounts = ladder.get("amounts")
    if not isinstance(thresholds, list) or not thresholds:
        errors.append("drawdown_ladder thresholds 必须是非空数组")
        thresholds = []
    if not isinstance(amounts, list) or not amounts:
        errors.append("drawdown_ladder amounts 必须是非空数组")
        amounts = []
    if thresholds and amounts and len(thresholds) != len(amounts):
        errors.append("drawdown_ladder thresholds 和 amounts 长度必须一致")
    previous = 0.0
    for value in thresholds:
        number = _number(value)
        if number is None or number <= 0 or number >= 1 or number <= previous:
            errors.append("drawdown_ladder thresholds 必须为递增的 0 到 1 之间数值")
            break
        previous = number
    for value in amounts:
        if _positive_number(value) is None:
            errors.append("drawdown_ladder amounts 必须为正数")
            break

    anchor_window = _positive_int(ladder.get("anchor_window", 120))
    annual_period = _positive_int(ladder.get("annual_period", 250))
    if anchor_window is None:
        errors.append("drawdown_ladder anchor_window 必须是正整数")
    if annual_period is None:
        errors.append("drawdown_ladder annual_period 必须是正整数")
    max_level = _positive_int(ladder.get("max_level", len(thresholds)))
    if max_level is None or (thresholds and max_level != len(thresholds)):
        errors.append("drawdown_ladder max_level 必须等于档位数量")
    if ladder.get("combine", "max") != "max":
        errors.append("drawdown_ladder combine 必须是 max")
    if ladder.get("requires_rsi_entry", True) is not True:
        errors.append("drawdown_ladder requires_rsi_entry 必须为 true")
    if ladder.get("reset", "new_anchor_high") != "new_anchor_high":
        errors.append("drawdown_ladder reset 必须是 new_anchor_high")
    for key in ("annual_boost_threshold", "annual_deep_threshold"):
        default = 0.0 if key == "annual_boost_threshold" else 0.05
        value = _number(ladder.get(key, default))
        if value is None or value < 0 or value >= 1:
            errors.append("drawdown_ladder {} 必须是 0 到 1 之间数值".format(key))
    return errors + _validate_fibonacci_ladder(fibonacci)


def _validate_fibonacci_ladder(ladder):
    if ladder is None:
        return []
    if not isinstance(ladder, dict):
        return ["fibonacci_ladder 必须是对象"]

    errors = []
    ratios = ladder.get("ratios")
    amounts = ladder.get("amounts")
    if not isinstance(ratios, list) or not ratios:
        errors.append("fibonacci_ladder ratios 必须是非空数组")
        ratios = []
    if not isinstance(amounts, list) or not amounts:
        errors.append("fibonacci_ladder amounts 必须是非空数组")
        amounts = []
    if ratios and amounts and len(ratios) != len(amounts):
        errors.append("fibonacci_ladder ratios 和 amounts 长度必须一致")

    previous = 0.0
    for value in ratios:
        number = _number(value)
        if number is None or number <= 0 or number >= 1 or number <= previous:
            errors.append("fibonacci_ladder ratios 必须为 0 到 1 之间的递增数值")
            break
        previous = number
    for value in amounts:
        if _positive_number(value) is None:
            errors.append("fibonacci_ladder amounts 必须为正数")
            break

    if _positive_int(ladder.get("anchor_window", 120)) is None:
        errors.append("fibonacci_ladder anchor_window 必须是正整数")
    if ladder.get("requires_rsi_entry", True) is not True:
        errors.append("fibonacci_ladder requires_rsi_entry 必须为 true")
    if ladder.get("crossing", "first_close_below") != "first_close_below":
        errors.append("fibonacci_ladder crossing 必须是 first_close_below")
    return errors


def _validate_rsi_contract(payload, indicators, sizing):
    entry = payload.get("entry") or {}
    exit_ = payload.get("exit") or {}
    uses_count_mode = entry.get("mode") == "count_conditions" or exit_.get(
        "mode"
    ) == "count_conditions"
    if not uses_count_mode:
        return []

    errors = []
    ids = []
    for item in indicators:
        if not isinstance(item, dict):
            errors.append("indicators 每一项必须是对象")
            continue
        indicator_id = str(item.get("id", ""))
        if not indicator_id:
            errors.append("indicators 每一项必须有 id")
        elif indicator_id in ids:
            errors.append("indicators id 不能重复：{}".format(indicator_id))
        ids.append(indicator_id)
        if item.get("type") == "sentiment":
            errors.extend(_validate_sentiment_indicator(item))
            continue
        if item.get("type") != "rsi":
            errors.append("仅支持 RSI 指标")
        if item.get("timeframe") not in {"1d", "1w"}:
            errors.append("RSI timeframe 必须是 1d 或 1w")
        if _positive_int(item.get("period")) is None:
            errors.append("RSI period 必须是正整数")
        if item.get("source", "close") != "close":
            errors.append("RSI source 必须是 close")
        if item.get("method", "wilder") != "wilder":
            errors.append("RSI method 必须是 wilder")
        if item.get("timeframe") == "1w" and item.get("completed_only") is not True:
            errors.append("周线 RSI 必须设置 completed_only=true")

    if entry.get("mode") == "count_conditions":
        errors.extend(
            _validate_condition_section(
                entry, "amount_by_count", {"1", "2"}, "买入"
            )
        )
    if exit_.get("mode") == "count_conditions":
        errors.extend(
            _validate_condition_section(
                exit_, "fraction_by_count", {"1", "2", "3"}, "卖出"
            )
        )
    if sizing and sizing.get("type") != "recurrent_cash":
        errors.append("count_conditions 策略必须使用 recurrent_cash 仓位类型")
    return errors


def _validate_sentiment_indicator(item):
    errors = []
    if not item.get("factor"):
        errors.append("sentiment indicator requires factor")
    if item.get("scope", "instrument") not in {"instrument", "market", "industry"}:
        errors.append("sentiment scope must be instrument, market, or industry")
    try:
        horizon = int(item.get("horizon"))
    except (TypeError, ValueError):
        horizon = None
    if horizon not in {1, 5, 20}:
        errors.append("sentiment horizon must be 1, 5, or 20")
    if item.get("representation", "raw") not in {"raw", "percentile"}:
        errors.append("sentiment representation must be raw or percentile")
    if item.get("cutoff", "15:00") != "15:00":
        errors.append("sentiment cutoff must be 15:00")
    if item.get("profile", "sentiment-baseline-v1") != "sentiment-baseline-v1":
        errors.append("unsupported sentiment profile")
    return errors


def _validate_condition_section(section, mapping_key, required_keys, label):
    errors = []
    conditions = section.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        errors.append("{} conditions 必须是非空数组".format(label))
    else:
        for condition in conditions:
            if not isinstance(condition, dict):
                errors.append("{} condition 必须是对象".format(label))
                continue
            if not condition.get("id") or not condition.get("indicator"):
                errors.append("{} condition 必须有 id 和 indicator".format(label))
            if condition.get("operator") not in {"<", "<=", ">", ">=", "=="}:
                errors.append("{} condition operator 不受支持".format(label))
            if _number(condition.get("value")) is None:
                errors.append("{} condition value 必须是数字".format(label))
    mapping = section.get(mapping_key)
    if not isinstance(mapping, dict) or not required_keys.issubset(mapping):
        errors.append("{} 必须完整配置 {}".format(label, mapping_key))
    else:
        for key in required_keys:
            value = _positive_number(mapping[key])
            if value is None:
                errors.append("{} {}[{}] 必须是正数".format(label, mapping_key, key))
    return errors


def _validate_execution(execution):
    if not execution:
        return []
    errors = []
    if execution.get("signal_at") not in {"close"}:
        errors.append("execution signal_at 必须是 close")
    if execution.get("fill_at") not in {"close"}:
        errors.append("execution fill_at 必须是 close")
    priority = execution.get("action_priority")
    if priority is not None and priority != ["EXIT", "ENTRY"]:
        errors.append("execution action_priority 必须是 [EXIT, ENTRY]")
    return errors


def _validate_buy_plan(config, label, lot_size):
    errors = []
    kind = str(config.get("type", "fixed_cash"))
    if kind not in _VALID_BUY_TYPES - {"all_in"}:
        errors.append("{} 的买入类型不支持：{}".format(label, kind))
    if kind == "fixed_cash" and _positive_number(config.get("amount", config.get("cash"))) is None:
        errors.append("{} 必须配置正数 amount".format(label))
    if kind == "fixed_quantity":
        quantity = _positive_int(config.get("quantity"))
        if quantity is None:
            errors.append("{} 必须配置正整数 quantity".format(label))
        elif quantity % lot_size:
            errors.append("{} quantity 必须按交易单位取整".format(label))
    if kind in {"cash_pct", "fixed_fraction"}:
        fraction = config.get("amount", config.get("fraction"))
        if _positive_number(fraction) is None or float(fraction) > 1:
            errors.append("{} 的现金比例必须大于 0 且不超过 1".format(label))
    if label == "periodic":
        frequency = config.get("frequency")
        if frequency not in {"weekly", "monthly", "dates"}:
            errors.append("periodic frequency 必须是 weekly、monthly 或 dates")
        if frequency == "monthly":
            day = _positive_int(config.get("day"))
            if day is None or day > 31:
                errors.append("periodic monthly day 必须在 1 到 31 之间")
        if frequency == "weekly":
            weekday = config.get("weekday", 0)
            if not isinstance(weekday, int) or weekday < 0 or weekday > 6:
                errors.append("periodic weekly weekday 必须在 0 到 6 之间")
        if frequency == "dates":
            dates = config.get("dates")
            if not isinstance(dates, list) or not dates:
                errors.append("periodic dates 必须是非空数组")
            else:
                for value in dates:
                    try:
                        date.fromisoformat(str(value))
                    except (TypeError, ValueError):
                        errors.append("periodic dates 必须使用 YYYY-MM-DD")
                        break
        if config.get("funding", "existing_cash") not in {
            "existing_cash",
            "external_contribution",
        }:
            errors.append("periodic funding 不支持")
        if config.get("non_trading_day", "skip") not in {"skip", "next_trading_day"}:
            errors.append("periodic non_trading_day 不支持")
        if config.get("execution", "next_open") not in {"scheduled_open", "next_open"}:
            errors.append("periodic execution 必须是 scheduled_open 或 next_open")
    return errors


def _validate_exit(exit_section, sizing):
    sell = exit_section.get("sell") or {}
    if not sell:
        return []
    errors = []
    kind = str(sell.get("type", "all"))
    if kind not in {"all", "percent", "quantity"}:
        return ["exit.sell 类型必须是 all、percent 或 quantity"]
    if kind == "percent":
        value = _positive_number(sell.get("value"))
        if value is None or value > 1:
            errors.append("exit.sell 百分比必须大于 0 且不超过 1")
    if kind == "quantity":
        value = _positive_int(sell.get("value"))
        lot_size = _positive_int((sizing or {}).get("lot_size", 100)) or 100
        if value is None:
            errors.append("exit.sell quantity 必须是正整数")
        elif value % lot_size:
            errors.append("exit.sell quantity 必须按交易单位取整")
    return errors


def _positive_int(value):
    try:
        value = int(value)
        return value if value > 0 and float(value) == float(value) else None
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value):
    try:
        value = int(value)
        return value if value >= 0 and float(value) == float(value) else None
    except (TypeError, ValueError):
        return None


def _positive_number(value):
    try:
        value = float(value)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        value = float(value)
        return value if value == value else None
    except (TypeError, ValueError):
        return None
