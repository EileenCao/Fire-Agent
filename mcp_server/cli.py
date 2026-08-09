"""Local command-line entry points used before any mobile notification is enabled."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, time
from pathlib import Path

from mcp_server.domain.models import DailyReportSchedule
from mcp_server.runtime import (
    build_calendar,
    build_market_provider,
    build_notifier,
    build_store,
    load_local_env,
)
from mcp_server.services.runner import DailyReportRunner


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
    if args.command in {"preview", "daily-report"}:
        notifier = build_notifier() if args.send else None
        if args.send and notifier is None:
            print("未配置 FEISHU_WEBHOOK_URL；本地验证请使用 preview 或 --no-send。", file=sys.stderr)
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
    result = {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "database": str(store.path),
        "database_exists": store.path.exists(),
        "calendar_source": calendar.source,
        "watchlist_count": len(store.list_watchlist()),
        "feishu_configured": bool(__import__("os").getenv("FEISHU_WEBHOOK_URL")),
        "network_send_performed": False,
    }
    _print_json(result)
    return 0


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
