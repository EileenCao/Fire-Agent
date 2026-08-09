"""SQLite persistence for the local research and notification workflow."""

import hashlib
import json
import sqlite3
from datetime import date, time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union
from zoneinfo import ZoneInfo

from mcp_server.domain.identifiers import normalize_ticker
from mcp_server.domain.models import DailyReportSchedule, WatchlistItem
from mcp_server.domain.strategy import StrategySpec


DEFAULT_DB_PATH = Path("data") / "stock_research.sqlite3"


class SQLiteStore:
    def __init__(self, path: Union[str, Path] = DEFAULT_DB_PATH):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS watchlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    market TEXT NOT NULL,
                    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('STOCK', 'ETF')),
                    name TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(code, market)
                );

                CREATE TABLE IF NOT EXISTS notification_channels (
                    id TEXT PRIMARY KEY,
                    channel_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    endpoint_env TEXT NOT NULL,
                    secret_env TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_schedules (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    timezone TEXT NOT NULL,
                    wake_time TEXT NOT NULL,
                    send_start TEXT NOT NULL,
                    send_end TEXT NOT NULL,
                    trading_days_only INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    report_date TEXT NOT NULL,
                    session TEXT NOT NULL,
                    data_as_of TEXT,
                    status TEXT NOT NULL,
                    content TEXT,
                    content_hash TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS delivery_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    channel_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 1,
                    content_format TEXT NOT NULL DEFAULT 'text',
                    status TEXT NOT NULL,
                    response_code INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES report_runs(id)
                );

                CREATE TABLE IF NOT EXISTS strategy_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(strategy_id, version)
                );

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_version TEXT,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS signal_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    signal_id TEXT NOT NULL UNIQUE,
                    scenario TEXT NOT NULL,
                    code TEXT NOT NULL,
                    side TEXT NOT NULL,
                    signal_date TEXT,
                    trade_date TEXT NOT NULL,
                    source_version TEXT,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES backtest_runs(id)
                );
                """
            )
            _ensure_column(connection, "delivery_attempts", "chunk_index", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "delivery_attempts", "chunk_count", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(connection, "delivery_attempts", "content_format", "TEXT NOT NULL DEFAULT 'text'")

    def save_strategy_version(
        self, spec: StrategySpec, status: str = "draft"
    ) -> Dict[str, Any]:
        if status not in {"draft", "approved", "active", "archived"}:
            raise ValueError("策略状态必须是 draft、approved、active 或 archived")
        if status in {"approved", "active"} and not spec.is_valid:
            raise ValueError("只有有效策略才能进入 approved 或 active 状态")
        payload = json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_versions
                    (strategy_id, version, name, status, spec_json, content_hash,
                     is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(strategy_id, version) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    spec_json = excluded.spec_json,
                    content_hash = excluded.content_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    spec.strategy_id,
                    spec.version,
                    spec.name,
                    status,
                    payload,
                    content_hash,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM strategy_versions
                WHERE strategy_id = ? AND version = ?
                """,
                (spec.strategy_id, spec.version),
            ).fetchone()
        return dict(row)

    def activate_strategy(self, strategy_id: str, version: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, status, spec_json FROM strategy_versions
                WHERE strategy_id = ? AND version = ?
                """,
                (strategy_id, version),
            ).fetchone()
            if row is None:
                raise ValueError("找不到策略版本：{}@{}".format(strategy_id, version))
            if row["status"] not in {"approved", "active"}:
                raise ValueError("只有 approved 策略版本才能激活")
            if not StrategySpec.from_dict(json.loads(row["spec_json"])).is_valid:
                raise ValueError("无效策略版本不能激活")
            connection.execute(
                "UPDATE strategy_versions SET is_active = 0, status = 'approved'"
                " WHERE is_active = 1"
            )
            connection.execute(
                """
                UPDATE strategy_versions
                SET is_active = 1, status = 'active', updated_at = ?
                WHERE strategy_id = ? AND version = ?
                """,
                (_utc_now(), strategy_id, version),
            )

    def list_strategy_versions(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_versions ORDER BY strategy_id, version"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_strategy(self) -> Optional[StrategySpec]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spec_json FROM strategy_versions WHERE is_active = 1 LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return StrategySpec.from_dict(json.loads(row["spec_json"]))

    def save_backtest_run(
        self, spec: StrategySpec, result: Dict[str, Any], status: str = "completed"
    ) -> Dict[str, Any]:
        timestamp = _utc_now()
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
        source_version = result.get("provenance", {}).get("source_version")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO backtest_runs
                    (strategy_id, strategy_version, status, source_version, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.strategy_id,
                    spec.version,
                    status,
                    source_version,
                    result_json,
                    timestamp,
                ),
            )
            run_id = cursor.lastrowid
            for scenario, scenario_result in result.get("scenarios", {}).items():
                for index, trade in enumerate(scenario_result.get("trades", [])):
                    signal_id = "{}:{}:{}".format(run_id, scenario, index)
                    evidence = {
                        "reason": trade.get("reason"),
                        "price": trade.get("price"),
                        "quantity": trade.get("quantity"),
                        "strategy_id": spec.strategy_id,
                        "strategy_version": spec.version,
                    }
                    connection.execute(
                        """
                        INSERT INTO signal_evidence
                            (run_id, signal_id, scenario, code, side, signal_date,
                             trade_date, source_version, evidence_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            signal_id,
                            scenario,
                            trade.get("code", ""),
                            trade.get("side", ""),
                            trade.get("signal_date"),
                            trade.get("date", ""),
                            source_version,
                            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                            timestamp,
                        ),
                    )
            row = connection.execute(
                "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return dict(row)

    def get_backtest_result(self, run_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["result"] = json.loads(record.pop("result_json"))
        return record

    def list_signal_evidence(
        self, run_id: Optional[int] = None, signal_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM signal_evidence"
        params = []
        if signal_id is not None:
            query += " WHERE signal_id = ?"
            params.append(signal_id)
        elif run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            result.append(item)
        return result

    def compare_backtest_results(self, run_ids: Iterable[int]) -> List[Dict[str, Any]]:
        values = []
        for run_id in run_ids:
            result = self.get_backtest_result(int(run_id))
            if result is None:
                raise ValueError("找不到回测运行记录：{}".format(run_id))
            scenarios = result["result"].get("scenarios", {})
            values.append({
                "run_id": result["id"],
                "strategy_id": result["strategy_id"],
                "strategy_version": result["strategy_version"],
                "metrics": {
                    name: scenario.get("metrics", {})
                    for name, scenario in scenarios.items()
                },
            })
        return values

    def add_watchlist_item(
        self,
        code: str,
        instrument_type: str = "STOCK",
        market: Optional[str] = None,
        name: Optional[str] = None,
        note: str = "",
    ) -> WatchlistItem:
        normalized_code, normalized_market = normalize_ticker(code, market)
        normalized_type = instrument_type.upper()
        if normalized_type not in {"STOCK", "ETF"}:
            raise ValueError("instrument_type 必须是 STOCK 或 ETF")
        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO watchlist_items
                    (code, market, instrument_type, name, note, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(code, market) DO UPDATE SET
                    instrument_type = excluded.instrument_type,
                    name = excluded.name,
                    note = excluded.note,
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_code,
                    normalized_market,
                    normalized_type,
                    name,
                    note,
                    timestamp,
                    timestamp,
                ),
            )
        return self._find_watchlist_item(normalized_code, normalized_market)

    def remove_watchlist_item(self, code: str, market: Optional[str] = None) -> bool:
        normalized_code, normalized_market = normalize_ticker(code, market)
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM watchlist_items WHERE code = ? AND market = ?",
                (normalized_code, normalized_market),
            )
            return cursor.rowcount > 0

    def list_watchlist(self, enabled_only: bool = True) -> List[WatchlistItem]:
        query = "SELECT * FROM watchlist_items"
        params: Iterable[Any] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY market, code"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_watchlist_from_row(row) for row in rows]

    def watchlist_version(self) -> str:
        payload = [
            {
                "code": item.code,
                "market": item.market,
                "instrument_type": item.instrument_type,
                "name": item.name,
                "note": item.note,
                "enabled": item.enabled,
                "updated_at": item.updated_at,
            }
            for item in self.list_watchlist(enabled_only=False)
        ]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def configure_daily_report(
        self,
        enabled: bool = True,
        timezone: str = "Asia/Shanghai",
        wake_time: time = time(12, 0),
        send_start: time = time(12, 3),
        send_end: time = time(12, 5),
        trading_days_only: bool = True,
    ) -> DailyReportSchedule:
        try:
            ZoneInfo(timezone)
        except Exception as exc:
            raise ValueError("日报时区无效：{}".format(timezone)) from exc
        if send_start > send_end or wake_time > send_start:
            raise ValueError("日报发送窗口必须晚于唤醒时间且起点不能晚于终点")
        schedule = DailyReportSchedule(
            enabled=enabled,
            timezone=timezone,
            wake_time=wake_time,
            send_start=send_start,
            send_end=send_end,
            trading_days_only=trading_days_only,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO report_schedules
                    (id, enabled, timezone, wake_time, send_start, send_end, trading_days_only)
                VALUES ('daily_watchlist', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    timezone = excluded.timezone,
                    wake_time = excluded.wake_time,
                    send_start = excluded.send_start,
                    send_end = excluded.send_end,
                    trading_days_only = excluded.trading_days_only
                """,
                (
                    int(enabled),
                    timezone,
                    wake_time.isoformat(timespec="minutes"),
                    send_start.isoformat(timespec="minutes"),
                    send_end.isoformat(timespec="minutes"),
                    int(trading_days_only),
                ),
            )
        return schedule

    def get_daily_report_schedule(self) -> DailyReportSchedule:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_schedules WHERE id = 'daily_watchlist'"
            ).fetchone()
        if row is None:
            return DailyReportSchedule()
        return DailyReportSchedule(
            enabled=bool(row["enabled"]),
            timezone=row["timezone"],
            wake_time=time.fromisoformat(row["wake_time"]),
            send_start=time.fromisoformat(row["send_start"]),
            send_end=time.fromisoformat(row["send_end"]),
            trading_days_only=bool(row["trading_days_only"]),
        )

    def register_feishu_channel(
        self,
        channel_id: str = "feishu-main",
        display_name: str = "飞书私人群",
        endpoint_env: str = "FEISHU_WEBHOOK_URL",
        secret_env: str = "FEISHU_WEBHOOK_SECRET",
    ) -> None:
        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO notification_channels
                    (id, channel_type, display_name, endpoint_env, secret_env, enabled, created_at, updated_at)
                VALUES (?, 'feishu_webhook', ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    endpoint_env = excluded.endpoint_env,
                    secret_env = excluded.secret_env,
                    updated_at = excluded.updated_at
                """,
                (channel_id, display_name, endpoint_env, secret_env, timestamp, timestamp),
            )

    def create_report_run(
        self,
        idempotency_key: str,
        report_date: date,
        session: str,
        data_as_of: Optional[str],
        status: str,
        content: Optional[str],
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = _utc_now()
        content_hash = (
            hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO report_runs
                    (idempotency_key, report_date, session, data_as_of, status, content,
                     content_hash, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    report_date.isoformat(),
                    session,
                    data_as_of,
                    status,
                    content,
                    content_hash,
                    error,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM report_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return dict(row)

    def update_report_run(
        self,
        run_id: int,
        status: str,
        error: Optional[str] = None,
        data_as_of: Optional[str] = None,
        content: Optional[str] = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE report_runs
                SET status = ?, error = ?, data_as_of = COALESCE(?, data_as_of),
                    content = COALESCE(?, content),
                    content_hash = CASE WHEN ? IS NULL THEN content_hash
                                       ELSE ? END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    error,
                    data_as_of,
                    content,
                    content,
                    hashlib.sha256(content.encode("utf-8")).hexdigest()
                    if content is not None
                    else None,
                    _utc_now(),
                    run_id,
                ),
            )

    def claim_report_run(
        self,
        idempotency_key: str,
        report_date: date,
        session: str,
        lease_seconds: int = 600,
    ) -> Dict[str, Any]:
        """Atomically claim a report run, allowing stale crashed runs to recover."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM report_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO report_runs
                        (idempotency_key, report_date, session, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'running', ?, ?)
                    """,
                    (idempotency_key, report_date.isoformat(), session, timestamp, timestamp),
                )
                claimed = True
            else:
                existing = dict(row)
                if existing["status"] == "sent":
                    claimed = False
                elif existing["status"] == "running" and not _is_stale(
                    existing.get("updated_at"), lease_seconds
                ):
                    claimed = False
                else:
                    connection.execute(
                        """
                        UPDATE report_runs
                        SET status = 'running', error = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (timestamp, existing["id"]),
                    )
                    claimed = True
            current = connection.execute(
                "SELECT * FROM report_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        result = dict(current)
        result["claimed"] = claimed
        return result

    def get_report_run(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return dict(row) if row else None

    def record_delivery_attempt(
        self,
        run_id: int,
        channel_id: str,
        attempt: int,
        status: str,
        response_code: Optional[int] = None,
        error: Optional[str] = None,
        chunk_index: int = 0,
        chunk_count: int = 1,
        content_format: str = "text",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO delivery_attempts
                    (run_id, channel_id, attempt, chunk_index, chunk_count,
                     content_format, status, response_code, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    channel_id,
                    attempt,
                    chunk_index,
                    chunk_count,
                    content_format,
                    status,
                    response_code,
                    error,
                    _utc_now(),
                ),
            )

    def notification_status(self) -> Dict[str, Any]:
        with self._connect() as connection:
            channel = connection.execute(
                """
                SELECT id, channel_type, display_name, endpoint_env, secret_env, enabled
                FROM notification_channels ORDER BY id LIMIT 1
                """
            ).fetchone()
            latest = connection.execute(
                "SELECT * FROM delivery_attempts ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "channel": dict(channel) if channel else None,
            "latest_delivery": dict(latest) if latest else None,
        }

    def _find_watchlist_item(self, code: str, market: str) -> WatchlistItem:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM watchlist_items WHERE code = ? AND market = ?",
                (code, market),
            ).fetchone()
        if row is None:
            raise RuntimeError("写入观察清单后无法读取记录")
        return _watchlist_from_row(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info({})".format(table)).fetchall()
    }
    if column not in columns:
        connection.execute(
            "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, definition)
        )


def _is_stale(value: Optional[str], lease_seconds: int) -> bool:
    from datetime import datetime, timedelta, timezone

    if not value:
        return True
    try:
        updated = datetime.fromisoformat(value)
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated > timedelta(seconds=lease_seconds)


def _watchlist_from_row(row: sqlite3.Row) -> WatchlistItem:
    return WatchlistItem(
        code=row["code"],
        market=row["market"],
        instrument_type=row["instrument_type"],
        name=row["name"],
        note=row["note"],
        enabled=bool(row["enabled"]),
        item_id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
