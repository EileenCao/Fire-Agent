"""Dependency-light MCP stdio server for the local stock research workflow."""

import json
import sys
from dataclasses import asdict
from datetime import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from mcp_server.calendar import TradingCalendar
from mcp_server.adapters.instrument_research import AStockDataInstrumentProvider
from mcp_server.dependencies import AStockDataSkillError, require_a_stock_data_skill
from mcp_server.domain.strategy import StrategySpec, validate_run_assumptions
from mcp_server.runtime import (
    build_calendar,
    build_historical_data_provider,
    build_market_provider,
    build_notifier,
    build_sentiment_providers,
    build_store,
)
from mcp_server.services.backtesting import BacktestEngine
from mcp_server.services.artifacts import write_backtest_artifacts
from mcp_server.services.backtest_pipeline import (
    benchmark_provider_code,
    enrich_backtest_result,
)
from mcp_server.services.observer import StrategyObserver
from mcp_server.services.provider_registry import ProviderRegistry
from mcp_server.services.research import InstrumentResearchService
from mcp_server.services.research_artifacts import (
    render_research_report,
    write_research_artifacts,
)
from mcp_server.services.data_cache import ParquetDataCache
from mcp_server.services.historical_data import (
    HistoricalDataResult,
    attach_data_provenance,
    resolve_strategy_window,
)
from mcp_server.services.runner import DailyReportRunner
from mcp_server.services.sentiment_backtest import attach_sentiment_snapshots
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
        _tool("update_external_position", "更新经用户确认的工作区外部场外持仓", {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "vehicle": {"type": "string"},
                "tracking_mode": {"type": "string"},
                "market_value": {"type": "number"},
                "unrealized_pnl": {"type": "number"},
                "as_of": {"type": "string"},
                "cutoff_time": {"type": "string"},
            },
            "required": ["market_value", "unrealized_pnl", "as_of"],
        }),
        _tool("get_external_position", "读取工作区外部场外持仓", {
            "type": "object",
            "properties": {"code": {"type": "string"}},
        }),
        _tool("get_signal_evidence", "读取策略信号的规则和数据证据", {
            "type": "object",
            "properties": {
                "signal_id": {"type": "string"},
                "run_id": {"type": "integer"},
            },
        }),
        _tool("research_instrument", "Research one stock or ETF with deterministic evidence", {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "market": {"type": "string", "enum": ["SH", "SZ", "BJ"]},
                "instrument_type": {"type": "string", "enum": ["STOCK", "ETF"]},
                "name": {"type": "string"},
                "refresh": {"type": "boolean"},
                "provider_id": {"type": "string"},
                "as_of": {"type": "string", "description": "YYYY-MM-DD"},
                "sections": {"type": "array", "items": {"type": "string"}},
                "strategy_id": {"type": "string"},
                "strategy_version": {"type": "string"},
                "analysis_mode": {"type": "string", "enum": ["single", "debate"]},
                "include_watchlist": {"type": "boolean"},
                "include_memory": {"type": "boolean"},
                "memory_query": {"type": "string"},
                "memory_max_bytes": {"type": "integer"},
                "include_sentiment": {"type": "boolean"},
                "sentiment_snapshot_id": {"type": "integer"},
            },
            "required": ["code"],
        }),
        _tool("get_market_data", "Get market and technical data for an instrument", {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "market": {"type": "string", "enum": ["SH", "SZ", "BJ"]},
                "instrument_type": {"type": "string", "enum": ["STOCK", "ETF"]},
                "provider_id": {"type": "string"},
                "as_of": {"type": "string"},
                "refresh": {"type": "boolean"},
            },
            "required": ["code"],
        }),
        _tool("get_fundamentals", "Get fundamental data and provenance", {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "market": {"type": "string", "enum": ["SH", "SZ", "BJ"]},
                "instrument_type": {"type": "string", "enum": ["STOCK", "ETF"]},
                "provider_id": {"type": "string"},
                "as_of": {"type": "string"},
                "refresh": {"type": "boolean"},
            },
            "required": ["code"],
        }),
        _tool("get_valuation", "Get valuation data and provenance", {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "market": {"type": "string", "enum": ["SH", "SZ", "BJ"]},
                "instrument_type": {"type": "string", "enum": ["STOCK", "ETF"]},
                "provider_id": {"type": "string"},
                "as_of": {"type": "string"},
                "refresh": {"type": "boolean"},
            },
            "required": ["code"],
        }),
        _tool("score_instrument", "Calculate the versioned transparent research score", {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "market": {"type": "string", "enum": ["SH", "SZ", "BJ"]},
                "instrument_type": {"type": "string", "enum": ["STOCK", "ETF"]},
                "provider_id": {"type": "string"},
                "as_of": {"type": "string"},
                "refresh": {"type": "boolean"},
            },
            "required": ["code"],
        }),
        _tool("get_research_context", "Get a size-bounded deterministic research context", {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "integer"},
                "max_bytes": {"type": "integer"},
            },
            "required": ["snapshot_id"],
        }),
        _tool("save_research_analysis", "Save an AI interpretation with evidence references", {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "integer"},
                "context_hash": {"type": "string"},
                "analysis": {"type": "object"},
            },
            "required": ["snapshot_id", "context_hash", "analysis"],
        }),
        _tool("get_research_snapshot", "Read one immutable research snapshot", {
            "type": "object",
            "properties": {"snapshot_id": {"type": "integer"}},
            "required": ["snapshot_id"],
        }),
        _tool("list_research_snapshots", "List historical instrument research snapshots", {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "market": {"type": "string"},
                "limit": {"type": "integer"},
            },
        }),
        _tool("get_research_evidence", "Read field-level evidence for a snapshot", {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "integer"},
                "evidence_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["snapshot_id"],
        }),
        _tool("sentiment_source_upsert", "Configure one explicit news or blogger sentiment source", {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "provider_id": {"type": "string", "default": "a-stock-data"},
                "platform": {"type": "string"},
                "source_type": {"type": "string", "enum": ["news", "blogger"]},
                "author_id": {"type": "string"},
                "display_name": {"type": "string"},
                "config": {"type": "object"},
                "enabled": {"type": "boolean"},
            },
            "required": ["source_id", "platform", "source_type"],
        }),
        _tool("sentiment_source_list", "List configured sentiment sources", {
            "type": "object",
            "properties": {"include_disabled": {"type": "boolean"}},
        }),
        _tool("sentiment_source_deactivate", "Deactivate one sentiment source", {
            "type": "object",
            "properties": {"source_id": {"type": "string"}},
            "required": ["source_id"],
        }),
        _tool("collect_sentiment_documents", "Collect public sentiment documents through the selected Provider", {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "provider_id": {"type": "string", "default": "a-stock-data"},
                "code": {"type": "string"},
                "market": {"type": "string", "enum": ["SH", "SZ", "BJ"]},
                "instrument_type": {"type": "string", "enum": ["STOCK", "ETF"]},
                "as_of": {"type": "string"},
                "refresh": {"type": "boolean"},
            },
            "required": ["source_id"],
        }),
        _tool("ingest_sentiment_document", "Ingest a user-submitted or Provider-normalized sentiment document", {
            "type": "object",
            "properties": {"document": {"type": "object"}},
            "required": ["document"],
        }),
        _tool("get_sentiment_extraction_context", "Get bounded context for Agent sentiment extraction", {
            "type": "object",
            "properties": {
                "document_ids": {"type": "array", "items": {"type": "string"}},
                "max_bytes": {"type": "integer"},
            },
            "required": ["document_ids"],
        }),
        _tool("save_sentiment_extraction", "Save versioned structured sentiment extraction after context validation", {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "context_hash": {"type": "string"},
                "extraction": {"type": "object"},
                "extraction_model": {"type": "string"},
                "prompt_version": {"type": "string"},
            },
            "required": ["document_id", "context_hash", "extraction"],
        }),
        _tool("backfill_sentiment_data", "Backfill selected sentiment sources over an explicit date range", {
            "type": "object",
            "properties": {
                "source_ids": {"type": "array", "items": {"type": "string"}},
                "code": {"type": "string"},
                "market": {"type": "string"},
                "instrument_type": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "refresh": {"type": "boolean"},
            },
        }),
        _tool("build_sentiment_snapshot", "Build and persist a deterministic sentiment factor snapshot", {
            "type": "object",
            "properties": {
                "document_ids": {"type": "array", "items": {"type": "string"}},
                "source_id": {"type": "string"},
                "snapshot_date": {"type": "string"},
                "cutoff": {"type": "string", "default": "15:00"},
                "scope_type": {"type": "string", "enum": ["market", "instrument", "industry"]},
                "scope_key": {"type": "string"},
                "trading_dates": {"type": "array", "items": {"type": "string"}},
                "history": {"type": "object"},
                "market_confirmation": {"type": "object"},
            },
            "required": ["snapshot_date", "scope_type"],
        }),
        _tool("get_sentiment_snapshot", "Read one immutable sentiment snapshot", {
            "type": "object",
            "properties": {"snapshot_id": {"type": "integer"}},
            "required": ["snapshot_id"],
        }),
        _tool("list_sentiment_snapshots", "List historical sentiment snapshots", {
            "type": "object",
            "properties": {
                "scope_type": {"type": "string"},
                "scope_key": {"type": "string"},
                "limit": {"type": "integer"},
            },
        }),
        _tool("get_sentiment_evidence", "Read evidence references for a sentiment snapshot", {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "integer"},
                "evidence_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["snapshot_id"],
        }),
        _tool("evaluate_sentiment_authors", "Evaluate blogger claims using completed return windows", {
            "type": "object",
            "properties": {
                "document_ids": {"type": "array", "items": {"type": "string"}},
                "as_of": {"type": "string"},
                "returns_by_target": {"type": "object"},
                "benchmark_returns_by_target": {"type": "object"},
            },
            "required": ["as_of", "returns_by_target", "benchmark_returns_by_target"],
        }),
        _tool("prepare_strategy_candidate_from_opinion", "Prepare an unapproved strategy candidate from blogger statements", {
            "type": "object",
            "properties": {
                "document_ids": {"type": "array", "items": {"type": "string"}},
                "source_run_id": {"type": "integer"},
            },
        }),
        _tool("prepare_memory", "prepare user long-term memory", {
            "type": "object",
            "properties": {"candidate": {"type": "object"}},
            "required": ["candidate"],
        }),
        _tool("save_memory", "save confirmed user long-term memory", {
            "type": "object",
            "properties": {
                "candidate": {"type": "object"},
                "approval_hash": {"type": "string"},
                "supersedes_ids": {"type": "array", "items": {"type": "integer"}},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["candidate", "approval_hash", "user_confirmed"],
        }),
        _tool("list_memories", "list user long-term memories", {
            "type": "object",
            "properties": {
                "include_inactive": {"type": "boolean"},
                "memory_type": {"type": "string"},
                "scope_type": {"type": "string"},
            },
        }),
        _tool("search_memories", "search user long-term memories", {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }),
        _tool("get_memory_context", "get relevant active memory context", {
            "type": "object",
            "properties": {
                "scope": {"type": "object"},
                "query": {"type": "string"},
                "max_items": {"type": "integer"},
                "max_bytes": {"type": "integer"},
            },
        }),
        _tool("archive_memory", "archive a user long-term memory", {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["memory_id", "user_confirmed"],
        }),
        _tool("forget_memory", "permanently delete a user long-term memory", {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["memory_id", "user_confirmed"],
        }),
        _tool("export_memories", "export long-term memories as JSON", {
            "type": "object",
            "properties": {"output_path": {"type": "string"}},
        }),
        _tool("preview_memory_import", "preview long-term memory import", {
            "type": "object",
            "properties": {"input_path": {"type": "string"}},
            "required": ["input_path"],
        }),
        _tool("import_memories", "import long-term memories after confirmation", {
            "type": "object",
            "properties": {
                "input_path": {"type": "string"},
                "import_hash": {"type": "string"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["input_path", "import_hash", "user_confirmed"],
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
                    "sentiment_snapshot_ids": {"type": "array", "items": {"type": "integer"}},
                }
            )
        if tool["name"] == "observe_active_strategy":
            tool["inputSchema"]["properties"]["positions"] = {"type": "object"}
            tool["inputSchema"]["properties"]["sentiment_snapshot_ids"] = {
                "type": "array", "items": {"type": "integer"}
            }
        if tool["name"] == "prepare_backtest_data":
            tool["inputSchema"]["properties"]["cache_dir"] = {"type": "string"}
            tool["inputSchema"]["properties"]["sentiment_snapshot_ids"] = {
                "type": "array", "items": {"type": "integer"}
            }
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
        research_service=None,
        provider_registry=None,
        sentiment_provider=None,
        sentiment_providers=None,
    ):
        self.store = store
        self.market_provider = market_provider
        self.notifier = notifier
        self.calendar = calendar or TradingCalendar()
        self.backtest_engine = backtest_engine or BacktestEngine()
        self.require_data_skill = require_data_skill
        self.historical_data_provider = historical_data_provider
        self.research_service = research_service
        self.provider_registry = provider_registry
        self.sentiment_provider = sentiment_provider
        self.sentiment_providers = dict(sentiment_providers or {})
        if sentiment_provider is not None:
            self.sentiment_providers.setdefault(
                getattr(sentiment_provider, "provider_id", "a-stock-data"),
                sentiment_provider,
            )
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

    def _update_external_position(self, args):
        position = self.store.save_external_position(
            code=args.get("code", "512890"),
            vehicle=args.get("vehicle", "代持场外基金"),
            tracking_mode=args.get("tracking_mode", "direct_copy"),
            market_value=float(args["market_value"]),
            unrealized_pnl=float(args["unrealized_pnl"]),
            as_of=args["as_of"],
            cutoff_time=args.get("cutoff_time", "15:00"),
        )
        return {"position": position}

    def _get_external_position(self, args):
        return {"position": self.store.get_external_position(args.get("code", "512890"))}

    def _watchlist_remove(self, args):
        return {
            "removed": self.store.remove_watchlist_item(
                args["code"], args.get("market")
            )
        }

    def _watchlist_list(self, args):
        return {"items": [asdict(item) for item in self.store.list_watchlist()]}

    def _research_instrument(self, args):
        service = self._research_service(args)
        snapshot = service.build(
            code=args["code"],
            market=args.get("market"),
            instrument_type=args.get("instrument_type"),
            name=args.get("name"),
            as_of=_parse_date(args.get("as_of")) if args.get("as_of") else None,
            sections=args.get("sections"),
            refresh=bool(args.get("refresh")),
        )
        snapshot["analysis_mode"] = args.get("analysis_mode", "single")
        if snapshot["analysis_mode"] not in {"single", "debate"}:
            raise ValueError("analysis_mode must be single or debate")
        self._attach_strategy_context(snapshot, args)
        self._attach_user_context(snapshot, args)
        self._attach_sentiment_context(snapshot, args)
        record = self.store.save_research_snapshot(snapshot)
        artifacts = write_research_artifacts(
            self.artifact_root,
            snapshot,
            int(record["id"]),
            created_at=record["created_at"],
        )
        self.store.update_research_artifacts(
            int(record["id"]), artifacts["artifact_dir"], "pending"
        )
        return {
            "snapshot_id": record["id"],
            "snapshot": snapshot,
            "artifacts": artifacts,
            "analysis_status": "pending",
        }

    def _get_market_data(self, args):
        return self._research_sections(args, ("market", "bars"))

    def _get_fundamentals(self, args):
        return self._research_sections(args, ("fundamentals",))

    def _get_valuation(self, args):
        return self._research_sections(args, ("valuation",))

    def _score_instrument(self, args):
        result = self._research_sections(
            args, ("market", "bars", "fundamentals", "valuation", "capital")
        )
        return {
            "instrument": result["instrument"],
            "scores": result["scores"],
            "technical": result["technical"],
            "valuation": result["valuation"],
            "sections": result["sections"],
            "evidence": result["evidence"],
            "provenance": result["provenance"],
            "warnings": result["warnings"],
        }

    def _get_research_context(self, args):
        return self.store.get_research_context(
            int(args["snapshot_id"]), max_bytes=args.get("max_bytes", 32768)
        )

    def _save_research_analysis(self, args):
        snapshot_id = int(args["snapshot_id"])
        saved = self.store.save_research_analysis(
            snapshot_id,
            str(args["context_hash"]),
            args.get("analysis") or {},
        )
        record = self.store.get_research_snapshot(snapshot_id)
        if record is None:
            raise ValueError("research snapshot not found: {}".format(snapshot_id))
        if record.get("artifact_dir"):
            artifacts = render_research_report(
                Path(record["artifact_dir"]), record["snapshot"], saved["analysis"]
            )
        else:
            artifacts = write_research_artifacts(
                self.artifact_root,
                record["snapshot"],
                snapshot_id,
                created_at=record.get("created_at"),
                analysis=saved["analysis"],
            )
        self.store.update_research_artifacts(
            snapshot_id, artifacts["artifact_dir"], "saved"
        )
        return {
            "snapshot_id": snapshot_id,
            "analysis_status": "saved",
            "analysis": saved,
            "artifacts": artifacts,
        }

    def _get_research_snapshot(self, args):
        record = self.store.get_research_snapshot(int(args["snapshot_id"]))
        if record is None:
            raise ValueError("research snapshot not found: {}".format(args["snapshot_id"]))
        return record

    def _list_research_snapshots(self, args):
        return {
            "snapshots": self.store.list_research_snapshots(
                code=args.get("code"),
                market=args.get("market"),
                limit=args.get("limit", 20),
            )
        }

    def _get_research_evidence(self, args):
        snapshot_id = int(args["snapshot_id"])
        if self.store.get_research_snapshot(snapshot_id) is None:
            raise ValueError("research snapshot not found: {}".format(snapshot_id))
        return {
            "snapshot_id": snapshot_id,
            "evidence": self.store.list_research_evidence(
                snapshot_id,
                evidence_id=args.get("evidence_id"),
                limit=args.get("limit", 100),
            ),
        }

    def _sentiment_source_upsert(self, args):
        source = dict(args.get("source") or args)
        config = dict(source.get("config") or {})
        if source.get("provider_id"):
            config.setdefault("provider_id", source["provider_id"])
        source["config"] = config
        return {"source": self.store.upsert_sentiment_source(source)}

    def _sentiment_source_list(self, args):
        return {
            "sources": self.store.list_sentiment_sources(
                include_disabled=bool(args.get("include_disabled"))
            )
        }

    def _sentiment_source_deactivate(self, args):
        source = self.store.deactivate_sentiment_source(args["source_id"])
        if source is None:
            raise ValueError("sentiment source not found: {}".format(args["source_id"]))
        return {"source": source}

    def _sentiment_source(self, source_id):
        for source in self.store.list_sentiment_sources(include_disabled=True):
            if source.get("source_id") == str(source_id):
                if not source.get("enabled"):
                    raise ValueError("sentiment source is disabled: {}".format(source_id))
                return source
        raise ValueError("sentiment source not found: {}".format(source_id))

    def _collect_sentiment_documents(self, args):
        if not self.sentiment_providers:
            raise RuntimeError(
                "sentiment provider 未配置；真实新闻数据需要 a-stock-data，受限来源请使用 ingest_sentiment_document"
            )
        source = self._sentiment_source(args["source_id"])
        configured_provider = str((source.get("config") or {}).get("provider_id") or "a-stock-data")
        requested_provider = str(args.get("provider_id") or configured_provider)
        provider = self.sentiment_providers.get(requested_provider)
        if requested_provider != configured_provider or provider is None:
            raise ValueError(
                "sentiment provider 必须显式选择且当前未配置：{}".format(requested_provider)
            )
        result = provider.collect_documents(
            source,
            code=args.get("code"),
            market=args.get("market"),
            instrument_type=args.get("instrument_type", "STOCK"),
            as_of=_parse_date(args.get("as_of")) if args.get("as_of") else None,
            refresh=bool(args.get("refresh")),
        )
        start_date = args.get("start_date")
        end_date = args.get("end_date")
        if start_date and end_date and str(start_date) > str(end_date):
            raise ValueError("sentiment backfill start_date cannot be after end_date")
        documents = []
        excluded = 0
        warnings = list(result.get("warnings", []))
        for item in result.get("documents", []):
            if not _sentiment_date_in_range(item.get("published_at"), start_date, end_date):
                excluded += 1
                continue
            documents.append(self.store.ingest_sentiment_document(item))
        coverage_dates = sorted(
            {
                str(item.get("published_at", ""))[:10]
                for item in documents
                if item.get("published_at")
            }
        )
        if excluded:
            warnings.append("{} 条内容不在请求的回补日期范围内，已排除".format(excluded))
        return {
            "source_id": source["source_id"],
            "documents": documents,
            "warnings": warnings,
            "coverage_dates": coverage_dates,
            "excluded_count": excluded,
        }

    def _ingest_sentiment_document(self, args):
        return {"document": self.store.ingest_sentiment_document(args["document"])}

    def _get_sentiment_extraction_context(self, args):
        return self.store.get_sentiment_extraction_context(
            args.get("document_ids") or [], max_bytes=args.get("max_bytes", 32768)
        )

    def _save_sentiment_extraction(self, args):
        from mcp_server.services.sentiment import normalize_extraction

        document_id = str(args["document_id"])
        extraction_payload = dict(args.get("extraction") or {})
        normalized = normalize_extraction(
            document_id,
            extraction_payload,
            model=str(args.get("extraction_model") or extraction_payload.get("extraction_model") or "agent"),
            prompt_version=str(args.get("prompt_version") or extraction_payload.get("prompt_version") or "sentiment-extract-v1"),
        )
        saved = self.store.save_sentiment_extraction(
            document_id, str(args["context_hash"]), normalized
        )
        return {"extraction": saved, "status": "saved"}

    def _backfill_sentiment_data(self, args):
        source_ids = args.get("source_ids") or [
            item["source_id"] for item in self.store.list_sentiment_sources()
        ]
        documents = []
        warnings = []
        for source_id in source_ids:
            result = self._collect_sentiment_documents(
                {
                    "source_id": source_id,
                    "code": args.get("code"),
                    "market": args.get("market"),
                    "instrument_type": args.get("instrument_type", "STOCK"),
                    "as_of": args.get("end_date"),
                    "refresh": args.get("refresh"),
                }
            )
            documents.extend(result.get("documents", []))
            warnings.extend(result.get("warnings", []))
        return {
            "documents": documents,
            "warnings": warnings,
            "start_date": args.get("start_date"),
            "end_date": args.get("end_date"),
            "coverage_dates": sorted(
                {str(item.get("published_at", ""))[:10] for item in documents if item.get("published_at")}
            ),
        }

    def _sentiment_documents_and_extractions(self, args):
        document_ids = [str(item) for item in (args.get("document_ids") or [])]
        if document_ids:
            documents = []
            for document_id in document_ids:
                document = self.store.get_sentiment_document(document_id)
                if document is None:
                    raise ValueError("sentiment document not found: {}".format(document_id))
                documents.append(document)
        else:
            documents = self.store.list_sentiment_documents(
                source_id=args.get("source_id"),
                before=args.get("before"),
                limit=args.get("limit", 1000),
            )
        extractions = []
        for document in documents:
            versions = self.store.list_sentiment_extractions(document["document_id"])
            if versions:
                extractions.append(versions[-1]["extraction"])
        return documents, extractions

    def _sentiment_factor_history(self, scope_type, scope_key):
        history = {}
        records = self.store.list_sentiment_snapshots(
            scope_type=scope_type, scope_key=scope_key, limit=252
        )
        for record in records:
            factors = record.get("snapshot", {}).get("factors", {})
            for horizon, horizon_factors in factors.items():
                target = history.setdefault(str(horizon), {})
                for factor, value in (horizon_factors or {}).items():
                    if not isinstance(value, Mapping):
                        continue
                    if value.get("status", "ok") != "ok" or value.get("value") is None:
                        continue
                    target.setdefault(str(factor), []).append(value["value"])
        return history

    def _build_sentiment_snapshot(self, args):
        from mcp_server.services.sentiment import build_sentiment_snapshot
        from mcp_server.services.sentiment_artifacts import write_sentiment_artifacts

        documents, extractions = self._sentiment_documents_and_extractions(args)
        author_performance = self.store.list_sentiment_author_performance(limit=500)
        author_weights = args.get("author_weights")
        if author_weights is None:
            author_weights = _author_weights(author_performance)
        history = (
            args.get("history")
            if "history" in args
            else self._sentiment_factor_history(args["scope_type"], args.get("scope_key"))
        )
        snapshot = build_sentiment_snapshot(
            documents,
            extractions,
            snapshot_date=args.get("snapshot_date") or _parse_date(None),
            cutoff=args.get("cutoff", "15:00"),
            scope_type=args["scope_type"],
            scope_key=args.get("scope_key"),
            trading_dates=args.get("trading_dates"),
            history=history or {},
            author_weights=author_weights,
            personalized_author_weights=args.get("personalized_author_weights"),
            market_confirmation=args.get("market_confirmation"),
        )
        snapshot["source_counts"] = _count_values(document.get("platform") for document in documents)
        snapshot["author_performance"] = author_performance
        snapshot["provenance"] = _sentiment_provenance(documents)
        snapshot["author_weights"] = author_weights
        record = self.store.save_sentiment_snapshot(snapshot)
        artifacts = write_sentiment_artifacts(
            self.artifact_root,
            snapshot,
            int(record["id"]),
            created_at=record["created_at"],
            author_performance=snapshot["author_performance"],
        )
        self.store.update_sentiment_artifacts(
            int(record["id"]), artifacts["artifact_dir"], "pending"
        )
        return {
            "snapshot_id": record["id"],
            "snapshot": snapshot,
            "artifacts": artifacts,
            "status": snapshot.get("backtest_eligibility", {}).get("status"),
        }

    def _get_sentiment_snapshot(self, args):
        record = self.store.get_sentiment_snapshot(int(args["snapshot_id"]))
        if record is None:
            raise ValueError("sentiment snapshot not found: {}".format(args["snapshot_id"]))
        return record

    def _list_sentiment_snapshots(self, args):
        return {
            "snapshots": self.store.list_sentiment_snapshots(
                scope_type=args.get("scope_type"),
                scope_key=args.get("scope_key"),
                limit=args.get("limit", 20),
            )
        }

    def _get_sentiment_evidence(self, args):
        snapshot_id = int(args["snapshot_id"])
        if self.store.get_sentiment_snapshot(snapshot_id) is None:
            raise ValueError("sentiment snapshot not found: {}".format(snapshot_id))
        return {
            "snapshot_id": snapshot_id,
            "evidence": self.store.list_sentiment_evidence(
                snapshot_id,
                evidence_id=args.get("evidence_id"),
                limit=args.get("limit", 100),
            ),
        }

    def _evaluate_sentiment_authors(self, args):
        from mcp_server.services.sentiment import evaluate_author_performance

        documents, extractions = self._sentiment_documents_and_extractions(args)
        performances = evaluate_author_performance(
            documents,
            extractions,
            returns_by_target=args.get("returns_by_target") or {},
            benchmark_returns_by_target=args.get("benchmark_returns_by_target") or {},
            as_of=args["as_of"],
        )
        saved = [self.store.save_sentiment_author_performance(item) for item in performances]
        return {"performance": saved}

    def _prepare_strategy_candidate_from_opinion(self, args):
        documents, extractions = self._sentiment_documents_and_extractions(args)
        document_by_id = {document["document_id"]: document for document in documents}
        statements = []
        evidence_refs = []
        for extraction in extractions:
            document_id = str(extraction.get("document_id"))
            for claim in extraction.get("claims", []) or []:
                statement = claim.get("strategy_statement")
                if not statement:
                    continue
                evidence_id = "sentiment:opinion:{}:{}".format(
                    document_id, claim.get("claim_id")
                )
                evidence_refs.append(evidence_id)
                statements.append(
                    {
                        "document_id": document_id,
                        "canonical_url": document_by_id.get(document_id, {}).get("canonical_url"),
                        "claim_id": claim.get("claim_id"),
                        "strategy_statement": statement,
                        "explicit_fields": sorted(statement),
                        "evidence_refs": [evidence_id],
                    }
                )
        return {
            "approval_required": True,
            "saved": False,
            "source_run_id": args.get("source_run_id"),
            "candidate": {"opinion_statements": statements},
            "missing_rules": [
                "entry and exit timing",
                "position sizing",
                "cost profile",
                "benchmark and risk-free rate",
                "missing-data behavior",
            ],
            "inferred_fields": [],
            "evidence_refs": sorted(set(evidence_refs)),
            "next_step": "与用户逐字段确认完整 diff 后，才允许 strategy-workbench 保存策略版本",
        }

    def _research_service(self, args):
        requested = str(args.get("provider_id") or "a-stock-data")
        if self.provider_registry is not None:
            return InstrumentResearchService(self.provider_registry.get(requested))
        if self.research_service is not None:
            actual = getattr(self.research_service.provider, "provider_id", "a-stock-data")
            if args.get("provider_id") and requested != actual:
                raise ValueError("research provider is not configured: {}".format(requested))
            return self.research_service
        raise RuntimeError(
            "instrument research provider is not configured; real data requires a-stock-data"
        )

    def _research_sections(self, args, sections):
        service = self._research_service(args)
        return service.build(
            code=args["code"],
            market=args.get("market"),
            instrument_type=args.get("instrument_type"),
            as_of=_parse_date(args.get("as_of")) if args.get("as_of") else None,
            sections=sections,
            refresh=bool(args.get("refresh")),
        )

    def _attach_strategy_context(self, snapshot, args):
        strategy_id = args.get("strategy_id")
        strategy_version = args.get("strategy_version")
        if not strategy_id and not strategy_version:
            snapshot["strategy_context"] = {"status": "not_requested"}
            return
        if not strategy_id or not strategy_version:
            raise ValueError("strategy_id and strategy_version must be provided together")
        record = self.store.get_strategy_version(strategy_id, strategy_version)
        if record is None:
            raise ValueError("strategy version not found: {}/{}".format(strategy_id, strategy_version))
        spec = StrategySpec.from_dict(record["strategy"])
        bars = ((snapshot.get("sections") or {}).get("bars") or {}).get("data") or []
        code = snapshot["instrument"]["code"]
        if code not in spec.universe:
            snapshot["strategy_context"] = {
                "status": "not_applicable",
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "reason": "instrument is not in the strategy universe",
            }
            return
        observation = StrategyObserver().observe(spec, {code: bars})
        snapshot["strategy_context"] = {
            "status": "ok",
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "observation": observation,
        }

    def _attach_user_context(self, snapshot, args):
        instrument = snapshot["instrument"]
        code = instrument["code"]
        market = instrument["market"]
        if args.get("include_watchlist"):
            items = [
                asdict(item)
                for item in self.store.list_watchlist()
                if item.code == code and item.market == market
            ]
            snapshot["watchlist_context"] = {
                "status": "matched" if items else "not_found",
                "items": items,
            }
        else:
            snapshot["watchlist_context"] = {"status": "not_requested"}
        if args.get("include_memory"):
            memory_context = self.store.get_memory_context(
                scope={
                    "strategy_id": args.get("strategy_id"),
                    "instruments": [code],
                },
                query=args.get("memory_query"),
                max_bytes=args.get("memory_max_bytes", 32768),
            )
            snapshot["memory_context"] = {
                **memory_context,
                "memory_refs": [
                    item.get("memory_id")
                    for item in memory_context.get("memories", [])
                    if item.get("memory_id") is not None
                ],
            }
        else:
            snapshot["memory_context"] = {
                "status": "not_requested",
                "memory_refs": [],
            }

    def _attach_sentiment_context(self, snapshot, args):
        if not args.get("include_sentiment") and args.get("sentiment_snapshot_id") is None:
            snapshot["sentiment_context"] = {"status": "not_requested"}
            return
        snapshot_id = args.get("sentiment_snapshot_id")
        record = None
        if snapshot_id is not None:
            record = self.store.get_sentiment_snapshot(int(snapshot_id))
        else:
            code = snapshot.get("instrument", {}).get("code")
            records = self.store.list_sentiment_snapshots(
                scope_type="instrument", scope_key=code, limit=1
            )
            record = records[0] if records else None
        if record is None:
            snapshot["sentiment_context"] = {
                "status": "not_found",
                "reason": "没有可用的情绪快照；请先采集、抽取并构建快照",
            }
            return
        snapshot["sentiment_context"] = {
            "status": "ok",
            "snapshot_id": record["id"],
            "snapshot": record["snapshot"],
        }

    def _prepare_memory(self, args):
        from mcp_server.services.memory import memory_candidate_hash, normalize_memory_candidate

        candidate = normalize_memory_candidate(args.get("candidate") or {})
        conflicts = self.store.find_memory_conflicts(candidate)
        return {
            "candidate": candidate,
            "approval_hash": memory_candidate_hash(candidate),
            "conflicts": conflicts,
        }

    def _save_memory(self, args):
        memory = self.store.save_memory(
            candidate=args.get("candidate") or {},
            approval_hash=args.get("approval_hash", ""),
            user_confirmed=bool(args.get("user_confirmed")),
            supersedes_ids=args.get("supersedes_ids"),
        )
        return {"memory": memory}

    def _list_memories(self, args):
        return {
            "memories": self.store.list_memories(
                include_inactive=bool(args.get("include_inactive")),
                memory_type=args.get("memory_type"),
                scope_type=args.get("scope_type"),
            )
        }

    def _search_memories(self, args):
        return {
            "memories": self.store.search_memories(
                args.get("query", ""), limit=args.get("limit", 20)
            )
        }

    def _get_memory_context(self, args):
        return self.store.get_memory_context(
            scope=args.get("scope"),
            query=args.get("query"),
            max_items=args.get("max_items", 20),
            max_bytes=args.get("max_bytes", 32768),
        )

    def _archive_memory(self, args):
        return self.store.archive_memory(
            int(args["memory_id"]), bool(args.get("user_confirmed"))
        )

    def _forget_memory(self, args):
        return self.store.forget_memory(
            int(args["memory_id"]), bool(args.get("user_confirmed"))
        )

    def _export_memories(self, args):
        output_path = args.get("output_path")
        if not output_path:
            output_path = Path(self.store.path).parent / "exports" / "memories.json"
        return self.store.export_memories(output_path)

    def _preview_memory_import(self, args):
        return self.store.preview_memory_import(args["input_path"])

    def _import_memories(self, args):
        return self.store.import_memories(
            input_path=args["input_path"],
            import_hash=args["import_hash"],
            user_confirmed=bool(args.get("user_confirmed")),
        )

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
            return spec, self._attach_sentiment_data(spec, args["data"], args), None
        if self.historical_data_provider is None:
            raise RuntimeError("未配置历史数据 Provider；请先完成工作区和 a-stock-data 配置")
        start_date, end_date = resolve_strategy_window(spec)
        fetched = self.historical_data_provider.fetch(spec.universe, start_date, end_date)
        attach_data_provenance(payload, fetched)
        resolved_spec = StrategySpec.from_dict(payload)
        return resolved_spec, self._attach_sentiment_data(resolved_spec, fetched.data, args), fetched

    def _attach_sentiment_data(self, spec, data, args):
        if not any(item.get("type") == "sentiment" for item in spec.indicators):
            return data
        snapshot_values = list(args.get("sentiment_snapshots") or [])
        for snapshot_id in args.get("sentiment_snapshot_ids") or []:
            record = self.store.get_sentiment_snapshot(int(snapshot_id))
            if record is None:
                raise ValueError("sentiment snapshot not found: {}".format(snapshot_id))
            snapshot_values.append(record["snapshot"])
        if not snapshot_values:
            return data
        return attach_sentiment_snapshots(data, snapshot_values)

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
        self._validate_sentiment_backtest_gate(spec, args, run_mode)
        benchmark_data, benchmark_fetched = self._resolve_benchmark_inputs(spec, args)
        result = self.backtest_engine.run(spec, data)
        if fetched is not None:
            result["provenance"].update(fetched.provenance)
        result["run_mode"] = run_mode
        if any(item.get("type") == "sentiment" for item in spec.indicators):
            result["sentiment_snapshot_ids"] = [
                int(item) for item in args.get("sentiment_snapshot_ids") or []
            ]
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

    def _validate_sentiment_backtest_gate(self, spec, args, run_mode):
        if not any(item.get("type") == "sentiment" for item in spec.indicators):
            return
        snapshot_ids = args.get("sentiment_snapshot_ids") or []
        if not snapshot_ids:
            if run_mode == "formal":
                raise ValueError("正式情绪回测必须提供 sentiment_snapshot_ids 并通过覆盖率门槛")
            return
        for snapshot_id in snapshot_ids:
            record = self.store.get_sentiment_snapshot(int(snapshot_id))
            if record is None:
                raise ValueError("sentiment snapshot not found: {}".format(snapshot_id))
            gate = record["snapshot"].get("backtest_eligibility") or record["snapshot"].get("backtest_gate") or {}
            if run_mode == "formal" and not gate.get("eligible") and gate.get("status") != "formal":
                raise ValueError("sentiment snapshot {} 未达到正式回测覆盖率和样本门槛".format(snapshot_id))

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
        code = benchmark_provider_code(spec.benchmark)
        try:
            fetched = self.historical_data_provider.fetch([code], start_date, end_date)
        except Exception as exc:
            fetched = _unavailable_historical_result(code, str(exc))
        return fetched.data, fetched

    def _get_backtest_result(self, args):
        record = self.store.get_backtest_result(int(args["run_id"]))
        if record is None:
            raise ValueError("找不到回测运行记录：{}".format(args["run_id"]))
        return record

    def _get_backtest_report_context(self, args):
        from mcp_server.services.analysis import build_report_context, _memory_scope

        run_id = int(args["run_id"])
        record = self.store.get_backtest_result(run_id)
        if record is None:
            raise ValueError("找不到回测运行记录：{}".format(run_id))
        evidence = self.store.list_signal_evidence(run_id)
        return build_report_context(
            record,
            evidence,
            memory_context=self.store.get_memory_context(_memory_scope(record, evidence)),
        )

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
        observation_data = args.get("data") or {}
        observation_data = self._attach_sentiment_data(spec, observation_data, args)
        result = StrategyObserver().observe(
            spec, observation_data, positions=args.get("positions") or {}
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
    skill = require_a_stock_data_skill()
    research_provider = AStockDataInstrumentProvider(
        market_provider=market_provider,
        historical_data_provider=historical_data_provider,
        skill=skill,
    )
    application = McpApplication(
        store=store,
        market_provider=market_provider,
        notifier=build_notifier(),
        calendar=build_calendar(require_workspace=True),
        require_data_skill=True,
        historical_data_provider=historical_data_provider,
        artifact_root=workspace.root / "artifacts",
        research_service=InstrumentResearchService(research_provider),
        provider_registry=ProviderRegistry({"a-stock-data": research_provider}),
        sentiment_providers=build_sentiment_providers(instrument_provider=research_provider),
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


def _sentiment_date_in_range(value, start_date=None, end_date=None):
    published = str(value or "")[:10]
    if len(published) != 10:
        return False
    if start_date and published < str(start_date)[:10]:
        return False
    if end_date and published > str(end_date)[:10]:
        return False
    return True


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


def _count_values(values: Iterable[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _sentiment_provenance(documents: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    documents = list(documents)
    providers = []
    for document in documents:
        metadata = document.get("metadata") or {}
        item = {
            "provider_id": metadata.get("provider_id"),
            "skill_name": metadata.get("skill_name"),
            "skill_version": metadata.get("skill_version"),
            "source_name": metadata.get("source_name"),
            "source_url": metadata.get("source_url"),
        }
        if item not in providers:
            providers.append(item)
    return {"providers": providers, "document_count": len(documents)}


def _author_weights(performance: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = {}
    for item in performance:
        author_id = item.get("author_id")
        weight = item.get("weight")
        horizon = item.get("horizon")
        if author_id is None or weight is None or horizon is None:
            continue
        result.setdefault(str(author_id), {})[str(horizon)] = float(weight)
    return result


if __name__ == "__main__":
    main()
