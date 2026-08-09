"""Local command-line entry points used before any mobile notification is enabled."""

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date, time
from pathlib import Path

from mcp_server.domain.models import DailyReportSchedule
from mcp_server.domain.strategy import StrategySpec
from mcp_server.dependencies import AStockDataSkillError, require_a_stock_data_skill
from mcp_server.runtime import (
    build_calendar,
    build_historical_data_provider,
    build_market_provider,
    build_notifier,
    build_store,
    load_local_env,
)
from mcp_server.services.runner import DailyReportRunner
from mcp_server.services.artifacts import write_backtest_artifacts
from mcp_server.services.backtesting import BacktestEngine
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
            project_root=root,
        )
    if args.command in {"preview", "daily-report"}:
        should_send = args.command == "daily-report" and args.send
        notifier = build_notifier() if should_send else None
        if should_send and notifier is None:
            print("未启用或未配置 Feishu；本地验证请使用 preview（默认不发送）。", file=sys.stderr)
            return 2
        runner = DailyReportRunner(
            store=store,
            market_provider=build_market_provider(root),
            notifier=notifier,
            calendar=build_calendar(root, require_workspace=True),
            require_authoritative_calendar=should_send,
        )
        result = runner.run(
            date.fromisoformat(args.report_date) if args.report_date else None,
            send=should_send,
        )
        if result.status == "previewed":
            print(result.message)
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
    project_root: Path,
    data_provider=None,
) -> int:
    try:
        skill = require_a_stock_data_skill()
        payload = _read_json(strategy_path)
        automatic_provenance = None
        data = {} if data_path is None else _read_json(data_path)
        if not isinstance(data, dict):
            raise ValueError("回测数据必须是按标的代码分组的 JSON 对象")
        policy = dict(payload.get("data_policy") or {})
        policy.setdefault("skill_name", skill.name)
        policy.setdefault("skill_version", skill.version)
        policy.setdefault("source_name", "a-stock-data")
        policy.setdefault("source_version", "a-stock-data:{}".format(skill.version))
        payload["data_policy"] = policy
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
        record = store.save_backtest_run(spec, result)
        artifacts = write_backtest_artifacts(output_dir, result, int(record["id"]))
        _print_json({"run_id": int(record["id"]), "artifacts": artifacts, "result": result})
        return 0
    except AStockDataSkillError as exc:
        _print_json({"status": "blocked", "error": str(exc)})
        return 2
    except (HistoricalDataError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _print_json({"status": "failed", "error": str(exc)})
        return 2


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
