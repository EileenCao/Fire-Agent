"""Runtime wiring kept separate from the deterministic domain services."""

import os
from pathlib import Path
from typing import Dict, Optional

from mcp_server.adapters.a_stock_data import TencentMarketDataProvider
from mcp_server.adapters.feishu import FeishuWebhookClient
from mcp_server.calendar import TradingCalendar
from mcp_server.storage import SQLiteStore


def load_local_env(project_root: Optional[Path] = None) -> Dict[str, str]:
    root = project_root or Path.cwd()
    values: Dict[str, str] = {}
    for path in (root / ".env", root / "config" / ".env"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def build_store(project_root: Optional[Path] = None) -> SQLiteStore:
    root = project_root or Path.cwd()
    load_local_env(root)
    configured = os.getenv("FIREAGENT_DB_PATH")
    path = Path(configured) if configured else root / "data" / "stock_research.sqlite3"
    store = SQLiteStore(path)
    store.initialize()
    store.register_feishu_channel()
    return store


def build_market_provider():
    return TencentMarketDataProvider()


def build_calendar(project_root: Optional[Path] = None) -> TradingCalendar:
    root = project_root or Path.cwd()
    configured = os.getenv("FIREAGENT_HOLIDAY_FILE")
    holiday_file = Path(configured) if configured else root / "data" / "trading_holidays.json"
    return TradingCalendar(holiday_file=holiday_file if holiday_file.exists() else None)


def build_notifier():
    url = os.getenv("FEISHU_WEBHOOK_URL")
    if not url:
        return None
    return FeishuWebhookClient(
        webhook_url=url,
        secret=os.getenv("FEISHU_WEBHOOK_SECRET") or None,
        max_payload_bytes=int(os.getenv("FEISHU_MAX_PAYLOAD_BYTES", "18432")),
    )
