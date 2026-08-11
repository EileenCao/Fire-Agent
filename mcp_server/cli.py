"""Local command-line entry points used before any mobile notification is enabled."""

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date, time
from pathlib import Path

from mcp_server.domain.models import DailyReportSchedule
from mcp_server.domain.strategy import StrategySpec, validate_run_assumptions
from mcp_server.dependencies import AStockDataSkillError, require_a_stock_data_skill
from mcp_server.runtime import (
    build_calendar,
    build_historical_data_provider,
    build_instrument_research_provider,
    build_market_provider,
    build_notifier,
    build_store,
    load_local_env,
)
from mcp_server.server import McpApplication
from mcp_server.services.runner import DailyReportRunner
from mcp_server.services.morning_report import build_morning_strategy_signal_builder
from mcp_server.services.artifacts import write_backtest_artifacts
from mcp_server.services.backtest_pipeline import (
    benchmark_provider_code,
    enrich_backtest_result,
)
from mcp_server.services.backtesting import BacktestEngine
from mcp_server.services.research import InstrumentResearchService
from mcp_server.services.historical_data import (
    HistoricalDataError,
    attach_data_provenance,
    resolve_strategy_window,
)
from mcp_server.sync import SyncError, sync_project
from mcp_server.workspace import WorkspaceError, initialize_workspace, load_workspace


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = Path.cwd()
    load_local_env(root)

    if args.command == "doctor":
        return _doctor(root)
    if args.command == "sync":
        return _sync(root)
    if args.command == "init":
        return _init_workspace(root, Path(args.workspace), args.overwrite)
    if args.command == "validate-strategy":
        return _validate_strategy_file(Path(args.file))

    try:
        workspace = load_workspace(root, required=True)
        store = build_store(root, require_workspace=True)
    except WorkspaceError as exc:
        _print_json({"status": "blocked", "error": str(exc)})
        return 2
    if args.command == "watchlist-add":
        item = store.add_watchlist_item(
            args.code,
            instrument_type=args.instrument_type,
            market=args.market,
            name=args.name,
            note=args.note,
        )
        _print_json(asdict(item))
        return 0
    if args.command == "watchlist-remove":
        _print_json({"removed": store.remove_watchlist_item(args.code, args.market)})
        return 0
    if args.command == "watchlist-list":
        _print_json([asdict(item) for item in store.list_watchlist()])
        return 0
    if args.command == "research":
        return _research_command(args, root, workspace, store)
    if args.command == "research-list":
        _print_json(
            store.list_research_snapshots(
                code=args.code,
                market=args.market,
                limit=args.limit,
            )
        )
        return 0
    if args.command == "memory-list":
        _print_json(
            store.list_memories(
                include_inactive=args.include_inactive,
                memory_type=args.memory_type,
                scope_type=args.scope_type,
            )
        )
        return 0
    if args.command == "memory-search":
        _print_json(store.search_memories(args.query, args.limit))
        return 0
    if args.command == "memory-export":
        output_path = (
            Path(args.output)
            if args.output
            else workspace.root / "exports" / "memories.json"
        )
        _print_json(store.export_memories(output_path))
        return 0
    if args.command == "memory-import":
        try:
            preview = store.preview_memory_import(args.file)
            if not args.confirm_hash:
                _print_json({"mode": "preview", **preview})
                return 0
            if args.confirm_hash != preview["import_hash"]:
                raise ValueError("导入哈希与预览结果不一致")
            imported = store.import_memories(
                args.file, args.confirm_hash, user_confirmed=True
            )
            _print_json({"mode": "import", **imported})
            return 0
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            _print_json({"status": "failed", "error": str(exc)})
            return 2
    if args.command == "configure":
        schedule = store.configure_daily_report(
            enabled=not args.disabled,
            timezone=args.timezone,
            wake_time=time.fromisoformat(args.wake_time),
            send_start=time.fromisoformat(args.send_start),
            send_end=time.fromisoformat(args.send_end),
            trading_days_only=not args.include_non_trading_days,
        )
        _print_json(asdict(schedule))
        return 0
    if args.command == "notification-status":
        value = store.notification_status()
        calendar = build_calendar(root, require_workspace=True)
        value["webhook_configured"] = build_notifier() is not None
        value["schedule"] = asdict(store.get_daily_report_schedule())
        value["calendar_source"] = calendar.source
        value["calendar_authoritative"] = calendar.is_authoritative
        value["network_send_performed"] = False
        _print_json(value)
        return 0
    if args.command == "notification-test":
        notifier = build_notifier()
        if notifier is None:
            _print_json(
                {
                    "status": "blocked",
                    "error": "未配置 Feishu；请在独立工作区 config/.env 设置 FEISHU_WEBHOOK_URL",
                }
            )
            return 2
        result = notifier.send_markdown(args.message or "FireAgent 飞书通知测试")
        _print_json({"status": "sent" if result.success else "failed", "delivery": asdict(result)})
        return 0 if result.success else 3
    if args.command == "run-backtest":
        strategy_path = Path(args.strategy) if args.strategy else workspace.strategy_path
        data_path = Path(args.data) if args.data else None
        output_dir = (
            Path(args.output_dir)
            if args.output_dir
            else workspace.formal_artifacts_dir
            if args.run_mode == "formal"
            else workspace.latest_artifacts_dir
        )
        return _run_backtest_command(
            strategy_path=strategy_path,
            data_path=data_path,
            output_dir=output_dir,
            store=store,
            run_mode=args.run_mode,
            confirm_cost_profile=args.confirm_cost_profile,
            confirm_position_sizing=args.confirm_position_sizing,
            confirm_benchmark=args.confirm_benchmark,
            confirm_risk_free_rate=args.confirm_risk_free_rate,
            project_root=root,
        )
    if args.command == "render-backtest-report":
        return _render_backtest_report_command(
            run_id=args.run_id,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            confirm_benchmark=args.confirm_benchmark,
            confirm_risk_free_rate=args.confirm_risk_free_rate,
            risk_free_rate_annual=args.risk_free_rate_annual,
            workspace=workspace,
            store=store,
        )
    if args.command in {"preview", "daily-report"}:
        should_send = args.command == "daily-report" and args.send
        notifier = build_notifier() if should_send else None
        if should_send and notifier is None:
            print("未启用或未配置 Feishu；本地验证请使用 preview（默认不发送）。", file=sys.stderr)
            return 2
        strategy_path = workspace.strategy_dir / "512890-core-rsi-profit-0.json"
        strategy_signal_builder = None
        if strategy_path.exists():
            strategy_signal_builder = build_morning_strategy_signal_builder(
                strategy_path,
                build_historical_data_provider(root),
                external_position_provider=store.get_external_position,
            )
        runner = DailyReportRunner(
            store=store,
            market_provider=build_market_provider(root),
            notifier=notifier,
            calendar=build_calendar(root, require_workspace=True),
            strategy_signal_builder=strategy_signal_builder,
            report_dir=workspace.reports_dir,
            require_authoritative_calendar=should_send,
        )
        result = runner.run(
            date.fromisoformat(args.report_date) if args.report_date else None,
            send=should_send,
        )
        if result.status == "previewed":
            _safe_print(result.message)
        else:
            _print_json({"status": result.status, "report_id": result.report_id, "message": result.message})
        return 0 if result.status not in {
            "delivery_failed",
            "missed_window",
            "blocked_invalid_schedule",
            "blocked_invalid_timezone",
            "blocked_report_date",
            "blocked_calendar_unavailable",
        } else 3
    parser.error("未知命令")
    return 2


def _parser():
    parser = argparse.ArgumentParser(description="FireAgent 本地股票研究与午间日报工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="检查本地运行环境，不访问飞书")
    subparsers.add_parser("sync", help="校验 Skill 并生成项目级 Codex MCP 配置")

    init = subparsers.add_parser("init", help="初始化用户提供的独立工作区")
    init.add_argument("--workspace", required=True, help="用户确认的独立工作区绝对路径")
    init.add_argument("--overwrite", action="store_true", help="明确覆盖已有工作区指针")

    add = subparsers.add_parser("watchlist-add", help="添加观察标的")
    add.add_argument("code")
    add.add_argument("--instrument-type", choices=["STOCK", "ETF"], default="STOCK")
    add.add_argument("--market", choices=["SH", "SZ", "BJ"])
    add.add_argument("--name")
    add.add_argument("--note", default="")

    remove = subparsers.add_parser("watchlist-remove", help="移除观察标的")
    remove.add_argument("code")
    remove.add_argument("--market", choices=["SH", "SZ", "BJ"])
    subparsers.add_parser("watchlist-list", help="列出观察清单")

    research = subparsers.add_parser("research", help="生成一个标的研究卡")
    research.add_argument("code")
    research.add_argument("--market", choices=["SH", "SZ", "BJ"])
    research.add_argument("--instrument-type", choices=["STOCK", "ETF"])
    research.add_argument("--name")
    research.add_argument("--provider-id", default="a-stock-data")
    research.add_argument("--as-of", help="YYYY-MM-DD")
    research.add_argument("--refresh", action="store_true")
    research.add_argument("--sections", help="逗号分隔，例如 market,bars,valuation")
    research.add_argument("--strategy-id")
    research.add_argument("--strategy-version")
    research.add_argument("--analysis-mode", choices=["single", "debate"], default="single")
    research.add_argument("--include-watchlist", action="store_true")
    research.add_argument("--include-memory", action="store_true")
    research.add_argument("--memory-query")

    research_list = subparsers.add_parser("research-list", help="列出历史研究快照")
    research_list.add_argument("--code")
    research_list.add_argument("--market", choices=["SH", "SZ", "BJ"])
    research_list.add_argument("--limit", type=int, default=20)

    memory_list = subparsers.add_parser("memory-list", help="列出长期记忆")
    memory_list.add_argument("--include-inactive", action="store_true")
    memory_list.add_argument("--memory-type")
    memory_list.add_argument("--scope-type")
    memory_search = subparsers.add_parser("memory-search", help="搜索长期记忆")
    memory_search.add_argument("query")
    memory_search.add_argument("--limit", type=int, default=20)
    memory_export = subparsers.add_parser("memory-export", help="导出长期记忆")
    memory_export.add_argument("--output")
    memory_import = subparsers.add_parser("memory-import", help="预览或导入长期记忆")
    memory_import.add_argument("--file", required=True)
    memory_import.add_argument("--confirm-hash")

    configure = subparsers.add_parser("configure", help="配置日报时间窗口")
    configure.add_argument("--wake-time", default="12:00")
    configure.add_argument("--send-start", default="12:03")
    configure.add_argument("--send-end", default="12:05")
    configure.add_argument("--timezone", default="Asia/Shanghai")
    configure.add_argument("--disabled", action="store_true")
    configure.add_argument("--include-non-trading-days", action="store_true")

    subparsers.add_parser("notification-status", help="查看通知配置和最近投递状态，不发送消息")
    test_notification = subparsers.add_parser("notification-test", help="发送一条明确标记的飞书测试消息")
    test_notification.add_argument("--message")

    validate = subparsers.add_parser("validate-strategy", help="验证策略文件")
    validate.add_argument("--file", required=True)

    run = subparsers.add_parser("run-backtest", help="运行本地日线回测")
    run.add_argument("--strategy", help="策略文件；省略时使用工作区 strategies\\strategy.json")
    run.add_argument("--data", help="可选的离线 JSON 日线数据；省略时自动通过 a-stock-data 获取")
    run.add_argument("--output-dir")
    run.add_argument("--run-mode", choices=["exploratory", "formal"], default="exploratory")
    run.add_argument("--confirm-cost-profile", action="store_true")
    run.add_argument("--confirm-position-sizing", action="store_true")
    run.add_argument("--confirm-benchmark", action="store_true")
    run.add_argument("--confirm-risk-free-rate", action="store_true")

    render = subparsers.add_parser(
        "render-backtest-report", help="从已保存回测事实非破坏性重渲染报告"
    )
    render.add_argument("--run-id", required=True, type=int)
    render.add_argument("--output-dir")
    render.add_argument("--confirm-benchmark", action="store_true")
    render.add_argument("--confirm-risk-free-rate", action="store_true")
    render.add_argument("--risk-free-rate-annual", type=float)

    preview = subparsers.add_parser("preview", help="生成但不发送午间观察日报")
    preview.add_argument("--report-date", help="YYYY-MM-DD，默认今天")
    daily = subparsers.add_parser("daily-report", help="生成并可发送午间观察日报")
    daily.add_argument("--report-date", help="YYYY-MM-DD，默认今天")
    daily.add_argument("--send", action="store_true", help="显式启用通知发送")
    return parser


def _doctor(root: Path) -> int:
    import platform
    import sqlite3

    load_local_env(root)
    store = build_store(root)
    calendar = build_calendar(root)
    try:
        workspace = load_workspace(root, required=False)
        workspace_result = (
            {"status": "ok", "path": str(workspace.root)}
            if workspace is not None
            else {
                "status": "missing",
                "message": "请先询问用户提供独立工作区，并运行 init --workspace <路径>",
            }
        )
    except WorkspaceError as exc:
        workspace_result = {"status": "invalid", "error": str(exc)}
    try:
        skill = require_a_stock_data_skill()
        skill_result = {
            "status": "ok",
            "name": skill.name,
            "version": skill.version,
            "path": str(skill.path),
        }
        skill_exit_code = 0
    except AStockDataSkillError as exc:
        skill_result = {"status": "missing", "error": str(exc)}
        skill_exit_code = 2
    result = {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "database": str(store.path),
        "database_exists": store.path.exists(),
        "workspace": workspace_result,
        "calendar_source": calendar.source,
        "a_stock_data_skill": skill_result,
        "memory": store.memory_status(),
        "watchlist_count": len(store.list_watchlist()),
        "feishu_configured": bool(
            os.getenv("FIREAGENT_ENABLE_FEISHU") == "1"
            and os.getenv("FEISHU_WEBHOOK_URL")
        ),
        "network_send_performed": False,
    }
    _print_json(result)
    return skill_exit_code


def _sync(root: Path) -> int:
    try:
        _print_json(sync_project(root))
        return 0
    except (AStockDataSkillError, SyncError, OSError) as exc:
        _print_json({"status": "blocked", "error": str(exc)})
        return 2


def _init_workspace(root: Path, workspace_path: Path, overwrite: bool) -> int:
    try:
        workspace = initialize_workspace(root, workspace_path, overwrite=overwrite)
        _print_json(
            {
                "status": "ok",
                "workspace": str(workspace.root),
                "pointer": str(workspace.pointer_path),
                "directories": {
                    "strategies": str(workspace.strategy_dir),
                    "config": str(workspace.config_dir),
                    "parquet": str(workspace.parquet_dir),
                    "artifacts": str(workspace.latest_artifacts_dir),
                    "database": str(workspace.db_path),
                },
                "git_sync": False,
            }
        )
        return 0
    except (WorkspaceError, OSError) as exc:
        _print_json({"status": "blocked", "error": str(exc)})
        return 2


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _research_command(args, root: Path, workspace, store) -> int:
    try:
        # Keep the production dependency gate explicit even when the Provider
        # is later replaced by a test double or another explicitly selected id.
        require_a_stock_data_skill()
        provider = build_instrument_research_provider(root)
        application = McpApplication(
            store=store,
            research_service=InstrumentResearchService(provider),
            artifact_root=workspace.root / "artifacts",
        )
        sections = (
            [item.strip() for item in args.sections.split(",") if item.strip()]
            if args.sections
            else None
        )
        result = application.call_tool(
            "research_instrument",
            {
                "code": args.code,
                "market": args.market,
                "instrument_type": args.instrument_type,
                "name": args.name,
                "provider_id": args.provider_id if args.provider_id != "a-stock-data" else None,
                "as_of": args.as_of,
                "refresh": args.refresh,
                "sections": sections,
                "strategy_id": args.strategy_id,
                "strategy_version": args.strategy_version,
                "analysis_mode": args.analysis_mode,
                "include_watchlist": args.include_watchlist,
                "include_memory": args.include_memory,
                "memory_query": args.memory_query,
            },
        )
        if result.get("isError"):
            _print_json({"status": "failed", "error": result["structuredContent"].get("error")})
            return 2
        _print_json(result["structuredContent"])
        return 0
    except (AStockDataSkillError, OSError, ValueError, TypeError, RuntimeError) as exc:
        _print_json({"status": "failed", "error": str(exc)})
        return 2


def _safe_print(value) -> None:
    try:
        print(value)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        fallback = str(value).encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        print(fallback)


def _validate_strategy_file(path: Path) -> int:
    try:
        payload = _read_json(path)
        spec = StrategySpec.from_dict(payload)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _print_json({"valid": False, "errors": [str(exc)]})
        return 2
    _print_json(
        {
            "valid": spec.is_valid,
            "strategy": spec.to_dict(),
            "errors": list(spec.validation_errors),
        }
    )
    return 0 if spec.is_valid else 2


def _run_backtest_command(
    strategy_path: Path,
    data_path: Path,
    output_dir: Path,
    store,
    run_mode: str,
    confirm_cost_profile: bool,
    confirm_position_sizing: bool,
    confirm_benchmark: bool,
    confirm_risk_free_rate: bool,
    project_root: Path,
    data_provider=None,
) -> int:
    try:
        skill = require_a_stock_data_skill()
        payload = _read_json(strategy_path)
        automatic_provenance = None
        data = {} if data_path is None else _read_json(data_path)
        if data_path is not None:
            data, cached_provenance = _normalize_backtest_data(data)
            automatic_provenance = cached_provenance
        if not isinstance(data, dict):
            raise ValueError("回测数据必须是按标的代码分组的 JSON 对象")
        policy = dict(payload.get("data_policy") or {})
        policy.setdefault("skill_name", skill.name)
        policy.setdefault("skill_version", skill.version)
        policy.setdefault("source_name", "a-stock-data")
        policy.setdefault("source_version", "a-stock-data:{}".format(skill.version))
        payload["data_policy"] = policy
        assumption_errors = validate_run_assumptions(
            payload,
            confirm_benchmark=confirm_benchmark,
            confirm_risk_free_rate=confirm_risk_free_rate,
        )
        if assumption_errors:
            _print_json({"status": "blocked", "error": "；".join(assumption_errors)})
            return 2
        spec = StrategySpec.from_dict(payload)
        if not spec.is_valid:
            _print_json({"valid": False, "errors": list(spec.validation_errors)})
            return 2
        if data_path is None:
            start_date, end_date = resolve_strategy_window(spec)
            provider = data_provider or build_historical_data_provider(project_root)
            fetched = provider.fetch(spec.universe, start_date, end_date)
            data = fetched.data
            automatic_provenance = fetched.provenance
            attach_data_provenance(payload, fetched)
            spec = StrategySpec.from_dict(payload)
            if not data:
                raise ValueError(
                    "Automatic historical data fetch failed; missing symbols: {}; errors: {}".format(
                        ", ".join(fetched.missing_symbols) or "unknown",
                        fetched.errors or "source returned no data",
                    )
                )
        benchmark_data = data if spec.benchmark and data_path is not None else {}
        benchmark_fetched = None
        if spec.benchmark and data_path is None:
            benchmark_code = benchmark_provider_code(spec.benchmark)
            try:
                benchmark_fetched = provider.fetch([benchmark_code], start_date, end_date)
                benchmark_data = benchmark_fetched.data
            except Exception as exc:
                benchmark_fetched = HistoricalDataResult(
                    data={},
                    provenance={"source_name": "a-stock-data"},
                    missing_symbols=[benchmark_code],
                    errors={benchmark_code: str(exc)},
                )
        if run_mode == "formal":
            if not confirm_cost_profile or not confirm_position_sizing:
                _print_json({"status": "blocked", "error": "正式回测必须明确确认成本模板和仓位方案"})
                return 2
            if not spec.cost_profile.get("template") or not spec.cost_profile.get("version"):
                _print_json({"status": "blocked", "error": "正式回测必须提供带版本的成本模板"})
                return 2
        result = BacktestEngine().run(spec, data)
        if automatic_provenance:
            result["provenance"].update(automatic_provenance)
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
        record = store.save_backtest_run(spec, result)
        artifacts = write_backtest_artifacts(
            output_dir, result, int(record["id"]), created_at=record["created_at"]
        )
        store.update_backtest_artifacts(
            int(record["id"]), artifacts["artifact_dir"], "pending"
        )
        _print_json(
            {
                "run_id": int(record["id"]),
                "artifacts": artifacts,
                "analysis_status": "pending",
                "result": result,
            }
        )
        return 0
    except AStockDataSkillError as exc:
        _print_json({"status": "blocked", "error": str(exc)})
        return 2
    except (HistoricalDataError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _print_json({"status": "failed", "error": str(exc)})
        return 2


def _render_backtest_report_command(
    run_id: int,
    output_dir: Path,
    confirm_benchmark: bool,
    confirm_risk_free_rate: bool,
    risk_free_rate_annual,
    workspace,
    store,
) -> int:
    try:
        record = store.get_backtest_result(int(run_id))
        if record is None:
            raise ValueError("找不到回测运行记录：{}".format(run_id))
        if not confirm_benchmark or not confirm_risk_free_rate:
            raise ValueError("重渲染报告前必须确认 benchmark 选择和年化无风险利率")
        result = dict(record["result"])
        assumptions = dict(result.get("assumptions") or {})
        selected = assumptions.get("benchmark")
        if "benchmark" not in assumptions:
            selected = None
        if risk_free_rate_annual is None:
            risk_free_rate_annual = assumptions.get("risk_free_rate_annual")
        if risk_free_rate_annual is None:
            raise ValueError("旧回测结果未记录无风险利率，请显式提供 --risk-free-rate-annual")
        result["assumptions"] = {
            "benchmark": selected,
            "risk_free_rate_annual": float(risk_free_rate_annual),
        }
        base_dir = output_dir
        if base_dir is None:
            mode = result.get("run_mode", "latest")
            base_dir = workspace.root / "artifacts" / (
                "formal" if mode == "formal" else "latest"
            )
        analysis_record = store.get_latest_backtest_analysis(int(run_id))
        analysis = analysis_record["analysis"] if analysis_record else None
        artifacts = write_backtest_artifacts(
            base_dir,
            result,
            int(run_id),
            created_at=record.get("created_at"),
            analysis=analysis,
        )
        store.update_backtest_artifacts(
            int(run_id), artifacts["artifact_dir"],
            "saved" if analysis_record else "pending",
        )
        _print_json(
            {
                "run_id": int(run_id),
                "artifacts": artifacts,
                "analysis_status": "saved" if analysis_record else "pending",
            }
        )
        return 0
    except (OSError, ValueError, TypeError) as exc:
        _print_json({"status": "failed", "error": str(exc)})
        return 2


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_backtest_data(payload):
    """Accept either the engine map or one cached symbol wrapper."""

    if (
        isinstance(payload, dict)
        and payload.get("code")
        and isinstance(payload.get("bars"), list)
    ):
        return {str(payload["code"]): payload["bars"]}, dict(
            payload.get("provenance") or {}
        )
    return payload, None


if __name__ == "__main__":
    raise SystemExit(main())
