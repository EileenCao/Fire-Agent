"""Dependency-light MCP stdio server for the local stock research workflow."""

import json
import sys
from dataclasses import asdict
from datetime import time
from typing import Any, Dict, Iterable, Optional

from mcp_server.calendar import TradingCalendar
from mcp_server.runtime import build_calendar, build_market_provider, build_notifier, build_store
from mcp_server.services.runner import DailyReportRunner


def tool_definitions() -> list:
    return [
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


class McpApplication:
    def __init__(
        self,
        store,
        market_provider=None,
        notifier=None,
        calendar: Optional[TradingCalendar] = None,
    ):
        self.store = store
        self.market_provider = market_provider
        self.notifier = notifier
        self.calendar = calendar or TradingCalendar()

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        args = arguments or {}
        try:
            handler = getattr(self, "_" + name)
        except AttributeError:
            return _error("未知工具：{}".format(name))
        try:
            value = handler(args)
            return _success(value)
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
        return {"schedule": asdict(schedule)}

    def _send_test_notification(self, args):
        if self.notifier is None:
            raise RuntimeError("未配置 FEISHU_WEBHOOK_URL")
        message = args.get("message") or "FireAgent 飞书午间日报通知测试"
        result = self.notifier.send_markdown(message)
        return {"delivery": asdict(result)}

    def _get_notification_status(self, args):
        value = self.store.notification_status()
        value["webhook_configured"] = self.notifier is not None
        value["schedule"] = asdict(self.store.get_daily_report_schedule())
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
    store = build_store()
    application = McpApplication(
        store=store,
        market_provider=build_market_provider(),
        notifier=build_notifier(),
        calendar=build_calendar(),
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


if __name__ == "__main__":
    main()
