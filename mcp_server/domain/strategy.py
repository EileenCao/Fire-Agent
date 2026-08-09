"""Versioned strategy contracts shared by the Agent, MCP and backtest engine."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mcp_server.domain.identifiers import normalize_ticker


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
    benchmark: Optional[str] = "000300"
    cost_profile: Dict[str, Any] = field(default_factory=dict)
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    data_policy: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    action_priority: List[str] = field(default_factory=lambda: ["EXIT", "ENTRY"])
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
            benchmark=payload.get("benchmark", "000300"),
            cost_profile=dict(payload.get("cost_profile") or {}),
            stop_loss_pct=payload.get("stop_loss_pct"),
            take_profit_pct=payload.get("take_profit_pct"),
            data_policy=dict(payload.get("data_policy") or {}),
            validation=dict(payload.get("validation") or {}),
            action_priority=list(payload.get("action_priority") or ["EXIT", "ENTRY"]),
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
            "benchmark": self.benchmark,
            "cost_profile": dict(self.cost_profile),
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "data_policy": dict(self.data_policy),
            "validation": dict(self.validation),
            "action_priority": list(self.action_priority),
            "plugin": dict(self.plugin) if self.plugin else None,
        }
