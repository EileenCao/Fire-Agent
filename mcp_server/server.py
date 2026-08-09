"""Dependency-light MCP stdio server for the local stock research workflow."""

import json
import sys
from dataclasses import asdict
from datetime import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from mcp_server.calendar import TradingCalendar
from mcp_server.dependencies import AStockDataSkillError, require_a_stock_data_skill
from mcp_server.domain.strategy import StrategySpec, validate_run_assumptions
from mcp_server.runtime import (
    build_calendar,
    build_historical_data_provider,
    build_market_provider,
    build_notifier,
    build_store,
)
from mcp_server.services.backtesting import BacktestEngine
from mcp_server.services.artifacts import write_backtest_artifacts
from mcp_server.services.backtest_pipeline import (
    benchmark_provider_code,
    enrich_backtest_result,
)
from mcp_server.services.observer import StrategyObserver
from mcp_server.services.data_cache import ParquetDataCache
from mcp_server.services.historical_data import (
    HistoricalDataResult,
    attach_data_provenance,
    resolve_strategy_window,
)
from mcp_server.services.runner import DailyReportRunner
from mcp_server.workspace import load_workspace


def tool_definitions() -> list:
    tools = [
        _tool("validate_strategy", "校验结构化策略并返回需要确认的错误和警告", {
            "type": "object",
            "properties": {"strategy": {"type": "object"}},
            "required": ["strategy"],
        }),
        _tool("save_strategy_version", "保存一个不可变策略版本", {
            "type": "object",
            "properties": {
                "strategy": {"type": "object"},
                "status": {"type": "string", "enum": ["draft", "approved"]},
                "parent_version": {"type": "string"},
                "source_run_id": {"type": "integer"},
                "change_set": {"type": "array"},
                "approved_diff_hash": {"type": "string"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["strategy"],
        }),
        _tool("activate_strategy", "激活一个已保存的策略版本", {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
                "version": {"type": "string"},
            },
            "required": ["strategy_id", "version"],
        }),
        _tool("prepare_backtest_data", "校验并准备回测数据快照", {
            "type": "object",
            "properties": {
                "strategy": {"type": "object"},
                "data": {"type": "object"},
            },
            "required": ["strategy"],
        }),
        _tool("run_backtest", "运行确定性的日线策略回测", {
            "type": "object",
            "properties": {
                "strategy": {"type": "object"},
                "data": {"type": "object"},
            },
            "required": ["strategy"],
        }),
        _tool("get_backtest_result", "读取已保存的回测结果", {
            "type": "object", "properties": {"run_id": {"type": "integer"}},
            "required": ["run_id"],
        }),
        _tool("get_backtest_report_context", "获取有大小限制的回测报告上下文", {
            "type": "object", "properties": {"run_id": {"type": "integer"}},
            "required": ["run_id"],
        }),
        _tool("save_backtest_analysis", "保存带证据引用的 AI 分析", {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer"},
                "context_hash": {"type": "string"},
                "analysis": {"type": "object"},
            },
            "required": ["run_id", "context_hash", "analysis"],
        }),
        _tool("prepare_strategy_revision", "生成待用户最终审批的策略 diff", {
            "type": "object",
            "properties": {
                "base_strategy": {"type": "object"},
                "strategy": {"type": "object"},
                "proposed_strategy": {"type": "object"},
                "source_run_id": {"type": "integer"},
                "change_details": {"type": "object"},
            },
            "required": ["strategy"],
        }),
        _tool("compare_backtests", "比较多个回测结果的核心指标", {
            "type": "object", "properties": {"run_ids": {"type": "array"}},
            "required": ["run_ids"],
        }),
        _tool("observe_active_strategy", "根据激活策略生成日维度规则观察", {
            "type": "object", "properties": {"data": {"type": "object"}},
            "required": ["data"],
        }),
        _tool("get_signal_evidence", "读取策略信号的规则和数据证据", {
            "type": "object",
            "properties": {
                "signal_id": {"type": "string"},
                "run_id": {"type": "integer"},
            },
        }),
        _tool("watchlist_add", "添加或恢复一个股票/ETF观察标的", {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "instrument_type": {"type": "string", "enum": ["STOCK", "ETF"]},
                "market": {"type": "string", "enum": ["SH", "SZ", "BJ"]},
                "name": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["code"],
        }),
        _tool("watchlist_remove", "从观察清单移除一个标的", {
            "type": "object",
            "properties": {"code": {"type": "string"}, "market": {"type": "string"}},
            "required": ["code"],
        }),
        _tool("watchlist_list", "列出当前启用的观察清单", {
            "type": "object", "properties": {},
        }),
        _tool("preview_daily_watchlist_report", "生成但不发送A股午间观察日报", {
            "type": "object",
            "properties": {"report_date": {"type": "string", "description": "YYYY-MM-DD"}},
        }),
        _tool("configure_daily_report", "配置交易日午间日报时间窗口", {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "timezone": {"type": "string"},
                "wake_time": {"type": "string", "description": "HH:MM"},
                "send_start": {"type": "string", "description": "HH:MM"},
                "send_end": {"type": "string", "description": "HH:MM"},
                "trading_days_only": {"type": "boolean"},
            },
        }),
        _tool("send_test_notification", "向已配置的飞书群发送测试消息", {
            "type": "object", "properties": {"message": {"type": "string"}},
        }),
        _tool("get_notification_status", "查看通知配置和最近一次投递状态", {
            "type": "object", "properties": {},
        }),
    ]
    for tool in tools:
        if tool["name"] == "run_backtest":
            tool["inputSchema"]["properties"].update(
                {
                    "run_mode": {"type": "string", "enum": ["exploratory", "formal"]},
                    "confirm_cost_profile": {"type": "boolean"},
                    "confirm_position_sizing": {"type": "boolean"},
                    "confirm_benchmark": {"type": "boolean"},
                    "confirm_risk_free_rate": {"type": "boolean"},
                }
            )
        if tool["name"] == "observe_active_strategy":
            tool["inputSchema"]["properties"]["positions"] = {"type": "object"}
        if tool["name"] == "prepare_backtest_data":
            tool["inputSchema"]["properties"]["cache_dir"] = {"type": "string"}
    return tools


class McpApplication:
    def __init__(
        self,
        store,
        market_provider=None,
        notifier=None,
        calendar: Optional[TradingCalendar] = None,
        backtest_engine: Optional[BacktestEngine] = None,
        require_data_skill: bool = False,
        historical_data_provider=None,
        artifact_root=None,
    ):
        self.store = store
        self.market_provider = market_provider
        self.notifier = notifier
        self.calendar = calendar or TradingCalendar()
        self.backtest_engine = backtest_engine or BacktestEngine()
        self.require_data_skill = require_data_skill
        self.historical_data_provider = historical_data_provider
        self.artifact_root = (
            Path(artifact_root)
            if artifact_root is not None
            else Path(store.path).parent / "artifacts"
        )

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        args = arguments or {}
        try:
            handler = getattr(self, "_" + name)
        except AttributeError:
            return _error("未知工具：{}".format(name))
        try:
            value = handler(args)
            return _success(value)
        except AStockDataSkillError as exc:
            return _error(str(exc))
        except (ValueError, TypeError) as exc:
            return _error(str(exc))
        except Exception as exc:
            return _error("工具执行失败：{}".format(exc))

    def _watchlist_add(self, args):
        item = self.store.add_watchlist_item(
            code=args["code"],
            instrument_type=args.get("instrument_type", "STOCK"),
            market=args.get("market"),
            name=args.get("name"),
            note=args.get("note", ""),
        )
        return {"item": asdict(item)}

    def _watchlist_remove(self, args):
        return {
            "removed": self.store.remove_watchlist_item(
                args["code"], args.get("market")
            )
        }

    def _watchlist_list(self, args):
        return {"items": [asdict(item) for item in self.store.list_watchlist()]}

    def _validate_strategy(self, args):
        spec = StrategySpec.from_dict(args.get("strategy") or {})
        return {
            "valid": spec.is_valid,
            "errors": list(spec.validation_errors),
            "strategy": spec.to_dict(),
        }

    def _save_strategy_version(self, args):
        spec = StrategySpec.from_dict(args.get("strategy") or {})
        status = args.get("status", "draft")
        revision_fields = {
            "parent_version",
            "source_run_id",
            "change_set",
            "approved_diff_hash",
        }
        is_revision = any(args.get(field) is not None for field in revision_fields)
        if is_revision:
            if not args.get("user_confirmed"):
                raise ValueError("保存策略修订前必须获得用户对完整 diff 的最终批准")
            required = ("parent_version", "change_set", "approved_diff_hash")
            missing = [field for field in required if args.get(field) in (None, "")]
            if missing:
                raise ValueError("策略修订缺少批准字段：{}".format(", ".join(missing)))
            from mcp_server.services.strategy_revision import verify_approved_revision

            verify_approved_revision(
                self.store,
                spec,
                args["parent_version"],
                args["change_set"],
                args["approved_diff_hash"],
                source_run_id=args.get("source_run_id"),
            )
            if args.get("source_run_id") is not None and self.store.get_backtest_result(
                int(args["source_run_id"])
            ) is None:
                raise ValueError("找不到来源回测运行：{}".format(args["source_run_id"]))
        record = self.store.save_strategy_version(
            spec,
            status=status,
            parent_version=args.get("parent_version"),
            source_run_id=args.get("source_run_id"),
            change_set=args.get("change_set"),
            approval_diff_hash=args.get("approved_diff_hash"),
        )
        return {
            "strategy": spec.to_dict(),
            "record": {
                "strategy_id": record["strategy_id"],
                "version": record["version"],
                "status": record["status"],
                "content_hash": record["content_hash"],
            },
        }

    def _activate_strategy(self, args):
        self.store.activate_strategy(args["strategy_id"], args["version"])
        return {"active": True, **args}

    def _resolve_backtest_inputs(self, args):
        payload = dict(args.get("strategy") or {})
        if self.require_data_skill:
            skill = require_a_stock_data_skill()
            policy = dict(payload.get("data_policy") or {})
            policy.setdefault("skill_name", skill.name)
            policy.setdefault("skill_version", skill.version)
            policy.setdefault("source_name", "a-stock-data")
            policy.setdefault("source_version", "a-stock-data:{}".format(skill.version))
            payload["data_policy"] = policy
        assumption_errors = validate_run_assumptions(
            payload,
            confirm_benchmark=bool(args.get("confirm_benchmark")),
            confirm_risk_free_rate=bool(args.get("confirm_risk_free_rate")),
        )
        if assumption_errors:
            raise ValueError("；".join(assumption_errors))
        spec = StrategySpec.from_dict(payload)
        if not spec.is_valid:
            raise ValueError("策略不可运行：{}".format("；".join(spec.validation_errors)))
        if "data" in args and args["data"] is not None:
            return spec, args["data"], None
        if self.historical_data_provider is None:
            raise RuntimeError("未配置历史数据 Provider；请先完成工作区和 a-stock-data 配置")
        start_date, end_date = resolve_strategy_window(spec)
        fetched = self.historical_data_provider.fetch(spec.universe, start_date, end_date)
        attach_data_provenance(payload, fetched)
        return StrategySpec.from_dict(payload), fetched.data, fetched

    def _prepare_backtest_data(self, args):
        payload = dict(args.get("strategy") or {})
        if self.require_data_skill:
            skill = require_a_stock_data_skill()
            policy = dict(payload.get("data_policy") or {})
            policy.setdefault("skill_name", skill.name)
            policy.setdefault("skill_version", skill.version)
            policy.setdefault("source_name", "a-stock-data")
            policy.setdefault("source_version", "a-stock-data:{}".format(skill.version))
            payload["data_policy"] = policy
        spec = StrategySpec.from_dict(payload)
        if not spec.is_valid:
            raise ValueError("策略不可运行：{}".format("；".join(spec.validation_errors)))
        spec, data, fetched = self._resolve_backtest_inputs(args)
        missing = [code for code in spec.universe if code not in data]
        result = {
            "ready": not missing,
            "symbols": list(data),
            "bar_count": sum(len(bars) for bars in data.values()),
            "missing_symbols": missing,
            "source_version": spec.data_policy.get("source_version", "a-stock-data:unknown"),
        }
        if fetched is not None:
            result["provenance"] = fetched.provenance
            result["errors"] = fetched.errors
            result["cache_paths"] = dict(fetched.cache_paths)
        cache_dir = args.get("cache_dir")
        if cache_dir:
            cache = ParquetDataCache(cache_dir)
            result["cache_paths"] = {
                code: str(
                    cache.write(
                        code,
                        data[code],
                        {
                            "source_name": spec.data_policy.get("source_name", "a-stock-data"),
                            "source_url": spec.data_policy.get("source_url"),
                            "source_version": spec.data_policy.get(
                                "source_version", "a-stock-data:unknown"
                            ),
                            "skill_name": spec.data_policy.get("skill_name", "a-stock-data"),
                            "skill_version": spec.data_policy.get("skill_version"),
                        },
                    )
                )
                for code in spec.universe
                if code in data
            }
        return result

    def _run_backtest(self, args):
        payload = dict(args.get("strategy") or {})
        if self.require_data_skill:
            skill = require_a_stock_data_skill()
            policy = dict(payload.get("data_policy") or {})
            policy.setdefault("skill_name", skill.name)
            policy.setdefault("skill_version", skill.version)
            policy.setdefault("source_name", "a-stock-data")
            policy.setdefault("source_version", "a-stock-data:{}".format(skill.version))
            payload["data_policy"] = policy
        spec = StrategySpec.from_dict(payload)
        if not spec.is_valid:
            raise ValueError("策略不可运行：{}".format("；".join(spec.validation_errors)))
        run_mode = args.get("run_mode", "exploratory")
        if run_mode not in {"exploratory", "formal"}:
            raise ValueError("run_mode 只能是 exploratory 或 formal")
        if run_mode == "formal":
            if not args.get("confirm_cost_profile") or not args.get("confirm_position_sizing"):
                raise ValueError("正式回测必须明确确认成本模板和仓位方案")
            if not spec.cost_profile.get("template") or not spec.cost_profile.get("version"):
                raise ValueError("正式回测必须提供带版本的成本模板")
        spec, data, fetched = self._resolve_backtest_inputs(args)
        benchmark_data, benchmark_fetched = self._resolve_benchmark_inputs(spec, args)
        result = self.backtest_engine.run(spec, data)
        if fetched is not None:
            result["provenance"].update(fetched.provenance)
        result["run_mode"] = run_mode
        enrich_backtest_result(
            result,
            spec,
            benchmark_data=benchmark_data,
            benchmark_provenance=(
                benchmark_fetched.provenance if benchmark_fetched else None
            ),
            benchmark_errors=(benchmark_fetched.errors if benchmark_fetched else None),
        )
        record = self.store.save_backtest_run(spec, result)
        mode_dir = self.artifact_root / ("formal" if run_mode == "formal" else "latest")
        artifacts = write_backtest_artifacts(
            mode_dir, result, int(record["id"]), created_at=record["created_at"]
        )
        self.store.update_backtest_artifacts(
            int(record["id"]), artifacts["artifact_dir"], "pending"
        )
        return {
            "run_id": record["id"],
            "result": result,
            "artifacts": artifacts,
            "analysis_status": "pending",
        }

    def _resolve_benchmark_inputs(self, spec, args):
        if not spec.benchmark:
            return {}, None
        data = args.get("benchmark_data")
        if data is None and isinstance(args.get("data"), dict):
            data = args.get("data")
        if isinstance(data, dict):
            return data, None
        if self.historical_data_provider is None:
            return {}, _unavailable_historical_result(
                benchmark_provider_code(spec.benchmark), "未配置历史数据 Provider"
            )
        start_date, end_date = resolve_strategy_window(spec)
        fetched = self.historical_data_provider.fetch(
            [benchmark_provider_code(spec.benchmark)], start_date, end_date
        )
        return fetched.data, fetched

    def _get_backtest_result(self, args):
        record = self.store.get_backtest_result(int(args["run_id"]))
        if record is None:
            raise ValueError("找不到回测运行记录：{}".format(args["run_id"]))
        return record

    def _get_backtest_report_context(self, args):
        from mcp_server.services.analysis import build_report_context

        run_id = int(args["run_id"])
        record = self.store.get_backtest_result(run_id)
        if record is None:
            raise ValueError("找不到回测运行记录：{}".format(run_id))
        return build_report_context(record, self.store.list_signal_evidence(run_id))

    def _save_backtest_analysis(self, args):
        from mcp_server.services.analysis import save_analysis_and_render

        return save_analysis_and_render(
            self.store,
            int(args["run_id"]),
            str(args["context_hash"]),
            args.get("analysis") or {},
        )

    def _prepare_strategy_revision(self, args):
        from mcp_server.services.strategy_revision import prepare_strategy_revision

        base = args.get("base_strategy")
        source_run_id = args.get("source_run_id")
        if base is None and source_run_id is not None:
            source = self.store.get_backtest_result(int(source_run_id))
            if source is not None:
                version = self.store.get_strategy_version(
                    source["strategy_id"], source["strategy_version"]
                )
                base = version.get("strategy") if version else None
        base = base or args.get("strategy")
        proposed = args.get("proposed_strategy") or args.get("strategy")
        return prepare_strategy_revision(
            base,
            proposed,
            source_run_id=source_run_id,
            change_details=args.get("change_details"),
        )

    def _compare_backtests(self, args):
        return self.store.compare_backtest_results(args.get("run_ids") or [])

    def _observe_active_strategy(self, args):
        spec = self.store.get_active_strategy()
        if spec is None:
            raise RuntimeError("尚未激活策略")
        if self.require_data_skill:
            skill = require_a_stock_data_skill()
            payload = spec.to_dict()
            policy = dict(payload.get("data_policy") or {})
            policy.setdefault("skill_name", skill.name)
            policy.setdefault("skill_version", skill.version)
            policy.setdefault("source_name", "a-stock-data")
            policy.setdefault("source_version", "a-stock-data:{}".format(skill.version))
            payload["data_policy"] = policy
            spec = StrategySpec.from_dict(payload)
        result = StrategyObserver().observe(
            spec, args.get("data") or {}, positions=args.get("positions") or {}
        )
        return {"strategy": spec.to_dict(), "observation": result}

    def _get_signal_evidence(self, args):
        signal_id = args.get("signal_id")
        run_id = args.get("run_id")
        evidence = self.store.list_signal_evidence(
            run_id=int(run_id) if run_id is not None else None,
            signal_id=signal_id,
        )
        return {
            "signal_id": signal_id,
            "run_id": run_id,
            "status": "ok" if evidence else "not_found",
            "evidence": evidence,
        }

    def _preview_daily_watchlist_report(self, args):
        if self.market_provider is None:
            raise RuntimeError("未配置行情数据适配器")
        target_date = _parse_date(args.get("report_date"))
        runner = DailyReportRunner(
            store=self.store,
            market_provider=self.market_provider,
            notifier=None,
            calendar=self.calendar,
        )
        result = runner.run(target_date, send=False)
        return {"status": result.status, "report_id": result.report_id, "content": result.message}

    def _configure_daily_report(self, args):
        schedule = self.store.configure_daily_report(
            enabled=args.get("enabled", True),
            timezone=args.get("timezone", "Asia/Shanghai"),
            wake_time=_parse_time(args.get("wake_time", "12:00")),
            send_start=_parse_time(args.get("send_start", "12:03")),
            send_end=_parse_time(args.get("send_end", "12:05")),
            trading_days_only=args.get("trading_days_only", True),
        )
        return {"schedule": _schedule_payload(schedule)}

    def _send_test_notification(self, args):
        if self.notifier is None:
            raise RuntimeError("未配置 FEISHU_WEBHOOK_URL")
        message = args.get("message") or "FireAgent 飞书午间日报通知测试"
        result = self.notifier.send_markdown(message)
        return {"delivery": asdict(result)}

    def _get_notification_status(self, args):
        value = self.store.notification_status()
        value["webhook_configured"] = self.notifier is not None
        value["schedule"] = _schedule_payload(self.store.get_daily_report_schedule())
        value["calendar_source"] = self.calendar.source
        value["calendar_authoritative"] = self.calendar.is_authoritative
        value["network_send_performed"] = False
        return value


class McpStdioServer:
    def __init__(self, application: McpApplication):
        self.application = application

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        request_id = message.get("id")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return _response(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fireagent-stock-research", "version": "0.1.0"},
                },
            )
        if method == "ping":
            return _response(request_id, {})
        if method == "tools/list":
            return _response(request_id, {"tools": tool_definitions()})
        if method == "tools/call":
            params = message.get("params") or {}
            result = self.application.call_tool(params.get("name", ""), params.get("arguments"))
            return _response(request_id, result)
        return _error_response(request_id, -32601, "方法不存在：{}".format(method))


def run_stdio(application: McpApplication) -> None:
    server = McpStdioServer(application)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = server.handle(message)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            sys.stderr.write("MCP 请求处理失败：{}\n".format(exc))
            sys.stderr.flush()


def main() -> None:
    store = build_store(require_workspace=True)
    workspace = load_workspace(Path.cwd(), required=True)
    market_provider = build_market_provider()
    historical_data_provider = build_historical_data_provider()
    application = McpApplication(
        store=store,
        market_provider=market_provider,
        notifier=build_notifier(),
        calendar=build_calendar(require_workspace=True),
        require_data_skill=True,
        historical_data_provider=historical_data_provider,
        artifact_root=workspace.root / "artifacts",
    )
    run_stdio(application)


def _tool(name: str, description: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": schema}


def _success(value: Any) -> Dict[str, Any]:
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    return {
        "isError": False,
        "content": [{"type": "text", "text": serialized}],
        "structuredContent": value,
    }


def _error(message: str) -> Dict[str, Any]:
    return {
        "isError": True,
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"error": message},
    }


def _response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id, code: int, message: str):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _parse_date(value: Optional[str]):
    from datetime import date

    return date.fromisoformat(value) if value else date.today()


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _schedule_payload(schedule) -> Dict[str, Any]:
    payload = asdict(schedule)
    for key in ("wake_time", "send_start", "send_end"):
        payload[key] = payload[key].isoformat(timespec="minutes")
    return payload


def _unavailable_historical_result(code: Optional[str], reason: str):
    return HistoricalDataResult(
        data={},
        provenance={"source_name": "a-stock-data", "missing_symbols": [code] if code else []},
        missing_symbols=[code] if code else [],
        errors={code or "benchmark": reason},
    )


if __name__ == "__main__":
    main()
