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
    build_market_provider,
    build_notifier,
    build_store,
    load_local_env,
)
from mcp_server.services.runner import DailyReportRunner
from mcp_server.services.artifacts import write_backtest_artifacts
from mcp_server.services.backtesting import BacktestEngine


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = Path.cwd()
    load_local_env(root)

    if args.command == "doctor":
        return _doctor(root)

    store = build_store(root)
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
    if args.command == "validate-strategy":
        return _validate_strategy_file(Path(args.file))
    if args.command == "run-backtest":
        return _run_backtest_command(
            strategy_path=Path(args.strategy),
            data_path=Path(args.data),
            output_dir=Path(args.output_dir),
            store=store,
            run_mode=args.run_mode,
            confirm_cost_profile=args.confirm_cost_profile,
            confirm_position_sizing=args.confirm_position_sizing,
        )
    if args.command in {"preview", "daily-report"}:
        notifier = build_notifier() if args.send else None
        if args.send and notifier is None:
            print("未启用或未配置 Feishu；本地验证请使用 preview（默认不发送）。", file=sys.stderr)
            return 2
        runner = DailyReportRunner(
            store=store,
            market_provider=build_market_provider(),
            notifier=notifier,
            calendar=build_calendar(root),
        )
        result = runner.run(
            date.fromisoformat(args.report_date) if args.report_date else date.today(),
            send=args.send,
        )
        if result.status == "previewed":
            print(result.message)
        else:
            _print_json({"status": result.status, "report_id": result.report_id, "message": result.message})
        return 0 if result.status not in {"delivery_failed"} else 3
    parser.error("未知命令")
    return 2


def _parser():
    parser = argparse.ArgumentParser(description="FireAgent 本地股票研究与午间日报工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="检查本地运行环境，不访问飞书")

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

    validate = subparsers.add_parser("validate-strategy", help="验证策略文件")
    validate.add_argument("--file", required=True)

    run = subparsers.add_parser("run-backtest", help="运行本地日线回测")
    run.add_argument("--strategy", required=True)
    run.add_argument("--data", required=True, help="JSON 格式的日线数据文件")
    run.add_argument("--output-dir", required=True)
    run.add_argument("--run-mode", choices=["exploratory", "formal"], default="exploratory")
    run.add_argument("--confirm-cost-profile", action="store_true")
    run.add_argument("--confirm-position-sizing", action="store_true")

    for name in ("preview", "daily-report"):
        command = subparsers.add_parser(name, help="生成午间观察日报")
        command.add_argument("--report-date", help="YYYY-MM-DD，默认今天")
        command.add_argument("--send", action="store_true", help="显式启用通知发送")
    return parser


def _doctor(root: Path) -> int:
    import platform
    import sqlite3

    load_local_env(root)
    store = build_store(root)
    calendar = build_calendar(root)
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
) -> int:
    try:
        skill = require_a_stock_data_skill()
        payload = _read_json(strategy_path)
        data = _read_json(data_path)
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
        if run_mode == "formal":
            if not confirm_cost_profile or not confirm_position_sizing:
                _print_json({"status": "blocked", "error": "正式回测必须明确确认成本模板和仓位方案"})
                return 2
            if not spec.cost_profile.get("template") or not spec.cost_profile.get("version"):
                _print_json({"status": "blocked", "error": "正式回测必须提供带版本的成本模板"})
                return 2
        result = BacktestEngine().run(spec, data)
        result["run_mode"] = run_mode
        record = store.save_backtest_run(spec, result)
        artifacts = write_backtest_artifacts(output_dir, result, int(record["id"]))
        _print_json({"run_id": int(record["id"]), "artifacts": artifacts, "result": result})
        return 0
    except AStockDataSkillError as exc:
        _print_json({"status": "blocked", "error": str(exc)})
        return 2
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _print_json({"status": "failed", "error": str(exc)})
        return 2


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
