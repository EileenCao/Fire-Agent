"""SQLite persistence for the local research and notification workflow."""

import hashlib
import json
import sqlite3
import uuid
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

                CREATE TABLE IF NOT EXISTS external_positions (
                    code TEXT PRIMARY KEY,
                    vehicle TEXT NOT NULL,
                    tracking_mode TEXT NOT NULL,
                    market_value REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    as_of TEXT NOT NULL,
                    cutoff_time TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                    parent_version TEXT,
                    source_run_id INTEGER,
                    change_set_json TEXT,
                    approval_diff_hash TEXT,
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
                    artifact_dir TEXT,
                    analysis_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backtest_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    context_hash TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, version),
                    FOREIGN KEY(run_id) REFERENCES backtest_runs(id)
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

                CREATE TABLE IF NOT EXISTS instrument_research_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    market TEXT NOT NULL,
                    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('STOCK', 'ETF')),
                    name TEXT,
                    data_as_of TEXT,
                    provider_id TEXT NOT NULL,
                    skill_version TEXT,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    artifact_dir TEXT,
                    analysis_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS instrument_research_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    evidence_id TEXT NOT NULL,
                    section TEXT NOT NULL,
                    field TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(snapshot_id, evidence_id),
                    FOREIGN KEY(snapshot_id) REFERENCES instrument_research_runs(id)
                );

                CREATE TABLE IF NOT EXISTS instrument_research_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    context_hash TEXT NOT NULL,
                    analysis_mode TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(snapshot_id, version),
                    FOREIGN KEY(snapshot_id) REFERENCES instrument_research_runs(id)
                );

                CREATE TABLE IF NOT EXISTS sentiment_sources (
                    source_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    author_id TEXT,
                    display_name TEXT,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sentiment_documents (
                    document_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    author_id TEXT,
                    author_name TEXT,
                    canonical_url TEXT,
                    published_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    event_fingerprint TEXT,
                    summary TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    targets_json TEXT NOT NULL DEFAULT '[]',
                    industries_json TEXT NOT NULL DEFAULT '[]',
                    concepts_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(platform, content_hash)
                );

                CREATE TABLE IF NOT EXISTS sentiment_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    context_hash TEXT NOT NULL,
                    extraction_json TEXT NOT NULL,
                    extraction_model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, version),
                    FOREIGN KEY(document_id) REFERENCES sentiment_documents(document_id)
                );

                CREATE TABLE IF NOT EXISTS sentiment_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT NOT NULL,
                    cutoff TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_key TEXT,
                    profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    artifact_dir TEXT,
                    analysis_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sentiment_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    evidence_id TEXT NOT NULL,
                    document_id TEXT,
                    factor TEXT,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(snapshot_id, evidence_id),
                    FOREIGN KEY(snapshot_id) REFERENCES sentiment_snapshots(id),
                    FOREIGN KEY(document_id) REFERENCES sentiment_documents(document_id)
                );

                CREATE TABLE IF NOT EXISTS sentiment_author_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author_id TEXT NOT NULL,
                    horizon INTEGER NOT NULL,
                    as_of TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    hit_rate REAL,
                    mean_absolute_return REAL,
                    mean_excess_return REAL,
                    weight REAL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_uuid TEXT NOT NULL UNIQUE,
                    lineage_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    memory_type TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_key TEXT,
                    topic_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    structured_value_json TEXT,
                    source_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    review_due_at TEXT,
                    supersedes_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    UNIQUE(lineage_id, version),
                    FOREIGN KEY(supersedes_id) REFERENCES memory_items(id)
                );
                """
            )
            _ensure_column(connection, "delivery_attempts", "chunk_index", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "delivery_attempts", "chunk_count", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(connection, "delivery_attempts", "content_format", "TEXT NOT NULL DEFAULT 'text'")
            _ensure_column(connection, "strategy_versions", "parent_version", "TEXT")
            _ensure_column(connection, "strategy_versions", "source_run_id", "INTEGER")
            _ensure_column(connection, "strategy_versions", "change_set_json", "TEXT")
            _ensure_column(connection, "strategy_versions", "approval_diff_hash", "TEXT")
            _ensure_column(connection, "backtest_runs", "artifact_dir", "TEXT")
            _ensure_column(
                connection,
                "backtest_runs",
                "analysis_status",
                "TEXT NOT NULL DEFAULT 'pending'",
            )
            _ensure_column(connection, "sentiment_snapshots", "artifact_dir", "TEXT")
            _ensure_column(connection, "sentiment_documents", "event_fingerprint", "TEXT")
            _ensure_column(
                connection,
                "sentiment_snapshots",
                "analysis_status",
                "TEXT NOT NULL DEFAULT 'pending'",
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_active_topic
                ON memory_items(memory_type, scope_type, scope_key, topic_key)
                WHERE status = 'active'
                """
            )
            _ensure_memory_fts(connection)

    def save_strategy_version(
        self,
        spec: StrategySpec,
        status: str = "draft",
        parent_version: Optional[str] = None,
        source_run_id: Optional[int] = None,
        change_set: Optional[List[Dict[str, Any]]] = None,
        approval_diff_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in {"draft", "approved", "active", "archived"}:
            raise ValueError("策略状态必须是 draft、approved、active 或 archived")
        if status in {"approved", "active"} and not spec.is_valid:
            raise ValueError("只有有效策略才能进入 approved 或 active 状态")
        payload = json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        timestamp = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM strategy_versions WHERE strategy_id = ? AND version = ?",
                (spec.strategy_id, spec.version),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != content_hash:
                    raise ValueError(
                        "策略版本不可覆盖：{}@{} 已存在不同内容".format(
                            spec.strategy_id, spec.version
                        )
                    )
                return dict(existing)
            connection.execute(
                """
                INSERT INTO strategy_versions
                    (strategy_id, version, name, status, spec_json, content_hash,
                     is_active, parent_version, source_run_id, change_set_json,
                     approval_diff_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.strategy_id,
                    spec.version,
                    spec.name,
                    status,
                    payload,
                    content_hash,
                    parent_version,
                    source_run_id,
                    json.dumps(change_set, ensure_ascii=False, sort_keys=True)
                    if change_set is not None
                    else None,
                    approval_diff_hash,
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

    def get_strategy_version(
        self, strategy_id: str, version: str
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_versions WHERE strategy_id = ? AND version = ?",
                (strategy_id, version),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["strategy"] = json.loads(value.pop("spec_json"))
        if value.get("change_set_json"):
            value["change_set"] = json.loads(value.pop("change_set_json"))
        return value

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
                    (strategy_id, strategy_version, status, source_version, result_json,
                     artifact_dir, analysis_status, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, 'pending', ?)
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

    def update_backtest_artifacts(
        self, run_id: int, artifact_dir: str, analysis_status: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if analysis_status is not None and analysis_status not in {
            "pending",
            "saved",
        }:
            raise ValueError("analysis_status 只能是 pending 或 saved")
        with self._connect() as connection:
            if analysis_status is None:
                connection.execute(
                    "UPDATE backtest_runs SET artifact_dir = ? WHERE id = ?",
                    (str(artifact_dir), int(run_id)),
                )
            else:
                connection.execute(
                    """
                    UPDATE backtest_runs
                    SET artifact_dir = ?, analysis_status = ?
                    WHERE id = ?
                    """,
                    (str(artifact_dir), analysis_status, int(run_id)),
                )
            row = connection.execute(
                "SELECT * FROM backtest_runs WHERE id = ?", (int(run_id),)
            ).fetchone()
        return dict(row) if row else None

    def save_backtest_analysis(
        self, run_id: int, context_hash: str, analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        timestamp = _utc_now()
        payload = json.dumps(analysis, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, version FROM backtest_analyses WHERE run_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (int(run_id),),
            ).fetchone()
            version = int(row["version"]) + 1 if row else 1
            connection.execute(
                """
                INSERT INTO backtest_analyses
                    (run_id, version, context_hash, analysis_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(run_id), version, context_hash, payload, timestamp),
            )
            connection.execute(
                "UPDATE backtest_runs SET analysis_status = 'saved' WHERE id = ?",
                (int(run_id),),
            )
            saved = connection.execute(
                "SELECT * FROM backtest_analyses WHERE run_id = ? AND version = ?",
                (int(run_id), version),
            ).fetchone()
        value = dict(saved)
        value["analysis"] = json.loads(value.pop("analysis_json"))
        return value

    def get_latest_backtest_analysis(self, run_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM backtest_analyses WHERE run_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (int(run_id),),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["analysis"] = json.loads(value.pop("analysis_json"))
        return value

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

    def upsert_sentiment_source(self, source: Dict[str, Any]) -> Dict[str, Any]:
        source_id = str(source.get("source_id") or "").strip()
        platform = str(source.get("platform") or "").strip()
        source_type = str(source.get("source_type") or "").strip()
        if not source_id or not platform or source_type not in {"news", "blogger"}:
            raise ValueError("sentiment source requires source_id, platform, and valid source_type")
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_sources
                    (source_id, platform, source_type, author_id, display_name,
                     config_json, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    platform=excluded.platform,
                    source_type=excluded.source_type,
                    author_id=excluded.author_id,
                    display_name=excluded.display_name,
                    config_json=excluded.config_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    source_id,
                    platform,
                    source_type,
                    source.get("author_id"),
                    source.get("display_name"),
                    json.dumps(source.get("config") or {}, ensure_ascii=False, sort_keys=True),
                    int(bool(source.get("enabled", True))),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM sentiment_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return _sentiment_source_from_row(row)

    def list_sentiment_sources(self, include_disabled: bool = False) -> List[Dict[str, Any]]:
        query = "SELECT * FROM sentiment_sources"
        if not include_disabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY platform, source_id"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [_sentiment_source_from_row(row) for row in rows]

    def deactivate_sentiment_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sentiment_sources SET enabled = 0, updated_at = ? WHERE source_id = ?",
                (_utc_now(), str(source_id)),
            )
            row = connection.execute(
                "SELECT * FROM sentiment_sources WHERE source_id = ?", (str(source_id),)
            ).fetchone()
        return _sentiment_source_from_row(row) if row else None

    def ingest_sentiment_document(self, document: Dict[str, Any]) -> Dict[str, Any]:
        from mcp_server.services.sentiment import normalize_document

        normalized = normalize_document(document)
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT document_id FROM sentiment_documents WHERE platform = ? AND content_hash = ?",
                (normalized["platform"], normalized["content_hash"]),
            ).fetchone()
            if existing is None and normalized.get("canonical_url"):
                existing = connection.execute(
                    "SELECT document_id FROM sentiment_documents WHERE platform = ? AND canonical_url = ?",
                    (normalized["platform"], normalized["canonical_url"]),
                ).fetchone()
            if existing is None and normalized.get("event_fingerprint"):
                existing = connection.execute(
                    "SELECT document_id FROM sentiment_documents WHERE platform = ? AND event_fingerprint = ?",
                    (normalized["platform"], normalized["event_fingerprint"]),
                ).fetchone()
            if existing is not None:
                row = connection.execute(
                    "SELECT * FROM sentiment_documents WHERE document_id = ?",
                    (existing["document_id"],),
                ).fetchone()
                return _sentiment_document_from_row(row)
            connection.execute(
                """
                INSERT INTO sentiment_documents
                    (document_id, platform, source_id, author_id, author_name,
                    canonical_url, published_at, collected_at, content_hash,
                     event_fingerprint, summary, document_type, targets_json, industries_json,
                     concepts_json, metadata_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["document_id"],
                    normalized["platform"],
                    normalized["source_id"],
                    normalized.get("author_id"),
                    normalized.get("author_name"),
                    normalized.get("canonical_url"),
                    normalized["published_at"],
                    normalized["collected_at"],
                    normalized["content_hash"],
                    normalized.get("event_fingerprint"),
                    normalized["summary"],
                    normalized["document_type"],
                    json.dumps(normalized.get("targets") or [], ensure_ascii=False),
                    json.dumps(normalized.get("shenwan_industries") or [], ensure_ascii=False),
                    json.dumps(normalized.get("concept_tags") or [], ensure_ascii=False),
                    json.dumps(normalized.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                    normalized.get("status", "collected"),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM sentiment_documents WHERE document_id = ?",
                (normalized["document_id"],),
            ).fetchone()
        return _sentiment_document_from_row(row)

    def get_sentiment_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sentiment_documents WHERE document_id = ?",
                (str(document_id),),
            ).fetchone()
        return _sentiment_document_from_row(row) if row else None

    def list_sentiment_documents(
        self,
        source_id: Optional[str] = None,
        document_type: Optional[str] = None,
        before: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(str(source_id))
        if document_type:
            clauses.append("document_type = ?")
            params.append(str(document_type))
        if before:
            clauses.append("published_at <= ?")
            params.append(str(before))
        query = "SELECT * FROM sentiment_documents"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY published_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_sentiment_document_from_row(row) for row in rows]

    def get_sentiment_extraction_context(
        self, document_ids: Iterable[str], max_bytes: int = 32768
    ) -> Dict[str, Any]:
        from mcp_server.services.sentiment import build_extraction_context

        documents = []
        for document_id in document_ids:
            document = self.get_sentiment_document(str(document_id))
            if document is None:
                raise ValueError("sentiment document not found: {}".format(document_id))
            documents.append(document)
        context = build_extraction_context(documents)
        encoded = json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > max(1024, int(max_bytes)):
            raise ValueError("sentiment extraction context exceeds max_bytes")
        return context

    def save_sentiment_extraction(
        self, document_id: str, context_hash: str, extraction: Dict[str, Any]
    ) -> Dict[str, Any]:
        document = self.get_sentiment_document(document_id)
        if document is None:
            raise ValueError("sentiment document not found: {}".format(document_id))
        context = self.get_sentiment_extraction_context([document_id])
        if str(context_hash) != context["context_hash"]:
            raise ValueError("sentiment extraction context hash is stale")
        if str(extraction.get("document_id")) != str(document_id):
            raise ValueError("sentiment extraction document_id does not match")
        evidence_refs = _sentiment_evidence_refs(extraction)
        unknown = sorted(set(evidence_refs) - set(context.get("evidence_ids", [])))
        if unknown:
            raise ValueError("sentiment extraction references unknown evidence: {}".format(", ".join(unknown)))
        model = str(extraction.get("extraction_model") or "unknown")
        prompt_version = str(extraction.get("prompt_version") or "unknown")
        payload = json.dumps(extraction, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM sentiment_extractions WHERE document_id = ?",
                (str(document_id),),
            ).fetchone()
            version = int(row["version"] or 0) + 1
            connection.execute(
                """
                INSERT INTO sentiment_extractions
                    (document_id, version, context_hash, extraction_json,
                     extraction_model, prompt_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(document_id),
                    version,
                    str(context_hash),
                    payload,
                    model,
                    prompt_version,
                    _utc_now(),
                ),
            )
            saved = connection.execute(
                "SELECT * FROM sentiment_extractions WHERE document_id = ? AND version = ?",
                (str(document_id), version),
            ).fetchone()
        return _sentiment_extraction_from_row(saved)

    def list_sentiment_extractions(self, document_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM sentiment_extractions"
        params: List[Any] = []
        if document_id is not None:
            query += " WHERE document_id = ?"
            params.append(str(document_id))
        query += " ORDER BY document_id, version"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_sentiment_extraction_from_row(row) for row in rows]

    def save_sentiment_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        scope = snapshot.get("scope") or {}
        snapshot_date = str(snapshot.get("snapshot_date") or "")
        cutoff = str(snapshot.get("cutoff") or "15:00")
        profile = str(snapshot.get("profile") or "sentiment-baseline-v1")
        if not snapshot_date or scope.get("type") not in {"market", "instrument", "industry"}:
            raise ValueError("sentiment snapshot requires snapshot_date and valid scope")
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sentiment_snapshots
                    (snapshot_date, cutoff, scope_type, scope_key, profile, status,
                     snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_date,
                    cutoff,
                    str(scope["type"]),
                    scope.get("key"),
                    profile,
                    str(snapshot.get("status") or "completed"),
                    payload,
                    _utc_now(),
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            for evidence in snapshot.get("evidence", []) or []:
                evidence_id = str(evidence.get("evidence_id") or "")
                if not evidence_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO sentiment_evidence
                        (snapshot_id, evidence_id, document_id, factor, evidence_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        evidence_id,
                        evidence.get("document_id"),
                        evidence.get("factor"),
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str),
                        _utc_now(),
                    ),
                )
            row = connection.execute(
                "SELECT * FROM sentiment_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        return _sentiment_snapshot_from_row(row)

    def update_sentiment_artifacts(
        self,
        snapshot_id: int,
        artifact_dir: str,
        analysis_status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if analysis_status is not None and analysis_status not in {"pending", "saved"}:
            raise ValueError("sentiment analysis_status must be pending or saved")
        with self._connect() as connection:
            if analysis_status is None:
                connection.execute(
                    "UPDATE sentiment_snapshots SET artifact_dir = ? WHERE id = ?",
                    (str(artifact_dir), int(snapshot_id)),
                )
            else:
                connection.execute(
                    "UPDATE sentiment_snapshots SET artifact_dir = ?, analysis_status = ? WHERE id = ?",
                    (str(artifact_dir), analysis_status, int(snapshot_id)),
                )
            row = connection.execute(
                "SELECT * FROM sentiment_snapshots WHERE id = ?", (int(snapshot_id),)
            ).fetchone()
        return _sentiment_snapshot_from_row(row) if row else None

    def get_sentiment_snapshot(self, snapshot_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sentiment_snapshots WHERE id = ?", (int(snapshot_id),)
            ).fetchone()
        return _sentiment_snapshot_from_row(row) if row else None

    def list_sentiment_snapshots(
        self, scope_type: Optional[str] = None, scope_key: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(str(scope_type))
        if scope_key:
            clauses.append("scope_key = ?")
            params.append(str(scope_key))
        query = "SELECT * FROM sentiment_snapshots"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_sentiment_snapshot_from_row(row) for row in rows]

    def list_sentiment_evidence(
        self, snapshot_id: int, evidence_id: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM sentiment_evidence WHERE snapshot_id = ?"
        params: List[Any] = [int(snapshot_id)]
        if evidence_id:
            query += " AND evidence_id = ?"
            params.append(str(evidence_id))
        query += " ORDER BY id LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            result.append(item)
        return result

    def save_sentiment_author_performance(self, performance: Dict[str, Any]) -> Dict[str, Any]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sentiment_author_performance
                    (author_id, horizon, as_of, sample_count, hit_rate,
                     mean_absolute_return, mean_excess_return, weight, status,
                     details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(performance["author_id"]),
                    int(performance["horizon"]),
                    str(performance["as_of"]),
                    int(performance.get("sample_count", 0)),
                    performance.get("hit_rate"),
                    performance.get("mean_absolute_return"),
                    performance.get("mean_excess_return"),
                    performance.get("weight"),
                    str(performance.get("status") or "ok"),
                    json.dumps(performance.get("details") or {}, ensure_ascii=False, sort_keys=True),
                    _utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM sentiment_author_performance WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        value = dict(row)
        value["details"] = json.loads(value.pop("details_json") or "{}")
        return value

    def list_sentiment_author_performance(
        self, author_id: Optional[str] = None, horizon: Optional[int] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if author_id:
            clauses.append("author_id = ?")
            params.append(str(author_id))
        if horizon:
            clauses.append("horizon = ?")
            params.append(int(horizon))
        query = "SELECT * FROM sentiment_author_performance"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["details"] = json.loads(value.pop("details_json") or "{}")
            result.append(value)
        return result

    def save_research_snapshot(
        self, snapshot: Dict[str, Any], status: str = "completed"
    ) -> Dict[str, Any]:
        instrument = snapshot.get("instrument") or {}
        code = str(instrument.get("code") or "")
        market = str(instrument.get("market") or "")
        instrument_type = str(instrument.get("instrument_type") or "").upper()
        if not code or market not in {"SH", "SZ", "BJ"}:
            raise ValueError("研究快照缺少规范化标的代码或市场")
        if instrument_type not in {"STOCK", "ETF"}:
            raise ValueError("研究快照 instrument_type 必须是 STOCK 或 ETF")
        provider_id = str(snapshot.get("provenance", {}).get("provider_id") or "unknown")
        skill_version = snapshot.get("provenance", {}).get("skill_version")
        timestamp = _utc_now()
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO instrument_research_runs
                    (code, market, instrument_type, name, data_as_of, provider_id,
                     skill_version, status, snapshot_json, artifact_dir,
                     analysis_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending', ?)
                """,
                (
                    code,
                    market,
                    instrument_type,
                    instrument.get("name"),
                    snapshot.get("data_as_of"),
                    provider_id,
                    skill_version,
                    status,
                    payload,
                    timestamp,
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            for item in snapshot.get("evidence", []) or []:
                evidence_id = str(item.get("evidence_id") or "")
                if not evidence_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO instrument_research_evidence
                        (snapshot_id, evidence_id, section, field, evidence_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        evidence_id,
                        str(item.get("section") or ""),
                        str(item.get("field") or ""),
                        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
                        timestamp,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM instrument_research_runs WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        return _research_run_from_row(row)

    def update_research_artifacts(
        self,
        snapshot_id: int,
        artifact_dir: str,
        analysis_status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if analysis_status is not None and analysis_status not in {"pending", "saved"}:
            raise ValueError("research analysis_status 只能是 pending 或 saved")
        with self._connect() as connection:
            if analysis_status is None:
                connection.execute(
                    "UPDATE instrument_research_runs SET artifact_dir = ? WHERE id = ?",
                    (str(artifact_dir), int(snapshot_id)),
                )
            else:
                connection.execute(
                    """
                    UPDATE instrument_research_runs
                    SET artifact_dir = ?, analysis_status = ?
                    WHERE id = ?
                    """,
                    (str(artifact_dir), analysis_status, int(snapshot_id)),
                )
            row = connection.execute(
                "SELECT * FROM instrument_research_runs WHERE id = ?",
                (int(snapshot_id),),
            ).fetchone()
        return _research_run_from_row(row) if row else None

    def get_research_snapshot(self, snapshot_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM instrument_research_runs WHERE id = ?",
                (int(snapshot_id),),
            ).fetchone()
        return _research_run_from_row(row) if row else None

    def list_research_snapshots(
        self,
        code: Optional[str] = None,
        market: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM instrument_research_runs"
        params: List[Any] = []
        clauses = []
        if code is not None:
            normalized_code, normalized_market = normalize_ticker(code, market)
            clauses.append("code = ?")
            params.append(normalized_code)
            if market is None:
                market = normalized_market
        if market is not None:
            clauses.append("market = ?")
            params.append(str(market).upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 100)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_research_run_from_row(row) for row in rows]

    def list_research_evidence(
        self, snapshot_id: int, evidence_id: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM instrument_research_evidence WHERE snapshot_id = ?"
        params: List[Any] = [int(snapshot_id)]
        if evidence_id is not None:
            query += " AND evidence_id = ?"
            params.append(str(evidence_id))
        query += " ORDER BY id LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["evidence"] = json.loads(value.pop("evidence_json"))
            result.append(value)
        return result

    def get_research_context(
        self, snapshot_id: int, max_bytes: int = 32768
    ) -> Dict[str, Any]:
        record = self.get_research_snapshot(snapshot_id)
        if record is None:
            raise ValueError("找不到研究快照：{}".format(snapshot_id))
        snapshot = dict(record["snapshot"])
        evidence = list(snapshot.get("evidence", []) or [])
        context = {
            "snapshot_id": int(snapshot_id),
            "instrument": snapshot.get("instrument"),
            "data_as_of": snapshot.get("data_as_of"),
            "provenance": snapshot.get("provenance"),
            "market": snapshot.get("market"),
            "technical": snapshot.get("technical"),
            "valuation": snapshot.get("valuation"),
            "scores": snapshot.get("scores"),
            "sections": snapshot.get("sections"),
            "strategy_context": snapshot.get("strategy_context"),
            "analysis_mode": snapshot.get("analysis_mode", "single"),
            "watchlist_context": snapshot.get("watchlist_context"),
            "memory_context": snapshot.get("memory_context"),
            "warnings": snapshot.get("warnings", []),
            "evidence": evidence,
        }
        while (
            len(json.dumps(context, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
            > max(1024, int(max_bytes))
            and context["evidence"]
        ):
            context["evidence"].pop()
        context["evidence_ids"] = [
            item.get("evidence_id") for item in context["evidence"] if item.get("evidence_id")
        ]
        context_hash = hashlib.sha256(
            json.dumps(context, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return {"context": context, "context_hash": context_hash, "evidence_ids": context["evidence_ids"]}

    def save_research_analysis(
        self, snapshot_id: int, context_hash: str, analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        current = self.get_research_context(snapshot_id)
        if context_hash != current["context_hash"]:
            raise ValueError("research context 已过期，请重新获取 context")
        if not isinstance(analysis, dict):
            raise ValueError("research analysis 必须是对象")
        refs = _research_evidence_refs(analysis)
        unknown = sorted(set(refs) - set(current["evidence_ids"]))
        if unknown:
            raise ValueError("research analysis 引用未知 evidence：{}".format(", ".join(unknown)))
        timestamp = _utc_now()
        payload = json.dumps(analysis, ensure_ascii=False, sort_keys=True, default=str)
        mode = str(analysis.get("mode") or "single")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT version FROM instrument_research_analyses "
                "WHERE snapshot_id = ? ORDER BY version DESC LIMIT 1",
                (int(snapshot_id),),
            ).fetchone()
            version = int(row["version"]) + 1 if row else 1
            connection.execute(
                """
                INSERT INTO instrument_research_analyses
                    (snapshot_id, version, context_hash, analysis_mode, analysis_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(snapshot_id), version, context_hash, mode, payload, timestamp),
            )
            connection.execute(
                "UPDATE instrument_research_runs SET analysis_status = 'saved' WHERE id = ?",
                (int(snapshot_id),),
            )
            saved = connection.execute(
                """
                SELECT * FROM instrument_research_analyses
                WHERE snapshot_id = ? AND version = ?
                """,
                (int(snapshot_id), version),
            ).fetchone()
        value = dict(saved)
        value["analysis"] = json.loads(value.pop("analysis_json"))
        return value

    def get_latest_research_analysis(self, snapshot_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM instrument_research_analyses
                WHERE snapshot_id = ? ORDER BY version DESC LIMIT 1
                """,
                (int(snapshot_id),),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["analysis"] = json.loads(value.pop("analysis_json"))
        return value

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

    def save_external_position(
        self,
        code: str,
        vehicle: str,
        tracking_mode: str,
        market_value: float,
        unrealized_pnl: float,
        as_of: str,
        cutoff_time: str = "15:00",
    ) -> Dict[str, Any]:
        normalized_code, _ = normalize_ticker(code, "SH")
        if market_value < 0:
            raise ValueError("external position market_value must be non-negative")
        if not vehicle or not tracking_mode or not as_of:
            raise ValueError("external position vehicle, tracking_mode, and as_of are required")
        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO external_positions
                    (code, vehicle, tracking_mode, market_value, unrealized_pnl,
                     as_of, cutoff_time, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    vehicle = excluded.vehicle,
                    tracking_mode = excluded.tracking_mode,
                    market_value = excluded.market_value,
                    unrealized_pnl = excluded.unrealized_pnl,
                    as_of = excluded.as_of,
                    cutoff_time = excluded.cutoff_time,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_code,
                    vehicle,
                    tracking_mode,
                    float(market_value),
                    float(unrealized_pnl),
                    as_of,
                    cutoff_time,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM external_positions WHERE code = ?",
                (normalized_code,),
            ).fetchone()
        return dict(row)

    def get_external_position(self, code: str = "512890") -> Optional[Dict[str, Any]]:
        normalized_code, _ = normalize_ticker(code, "SH")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM external_positions WHERE code = ?",
                (normalized_code,),
            ).fetchone()
        return dict(row) if row is not None else None

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

    def find_memory_conflicts(self, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
        from mcp_server.services.memory import memory_conflict_key, normalize_memory_candidate

        normalized = normalize_memory_candidate(candidate)
        memory_type, scope_type, scope_key, topic_key = memory_conflict_key(normalized)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_items
                WHERE status = 'active' AND memory_type = ? AND scope_type = ?
                  AND scope_key IS ? AND topic_key = ?
                ORDER BY id
                """,
                (memory_type, scope_type, scope_key, topic_key),
            ).fetchall()
        return [_memory_from_row(row) for row in rows]

    def memory_status(self) -> Dict[str, Any]:
        with self._connect() as connection:
            memory_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_items'"
            ).fetchone()
            fts_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_items_fts'"
            ).fetchone()
            count = 0
            if memory_table:
                count = int(
                    connection.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
                )
        return {
            "status": "ok" if memory_table else "missing",
            "table": bool(memory_table),
            "fts5": bool(fts_table),
            "count": count,
        }

    def save_memory(
        self,
        candidate: Dict[str, Any],
        approval_hash: str,
        user_confirmed: bool,
        supersedes_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        from mcp_server.services.memory import (
            memory_candidate_hash,
            memory_conflict_key,
            normalize_memory_candidate,
        )

        if not user_confirmed:
            raise ValueError("保存长期记忆前必须获得用户明确确认")
        normalized = normalize_memory_candidate(candidate)
        if approval_hash != memory_candidate_hash(normalized):
            raise ValueError("长期记忆候选已变化，审批哈希失效")
        expected_ids = sorted(int(value) for value in (supersedes_ids or []))
        memory_type, scope_type, scope_key, topic_key = memory_conflict_key(normalized)
        content_hash = memory_candidate_hash(normalized)
        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_rows = connection.execute(
                """
                SELECT * FROM memory_items
                WHERE status = 'active' AND memory_type = ? AND scope_type = ?
                  AND scope_key IS ? AND topic_key = ?
                ORDER BY id
                """,
                (memory_type, scope_type, scope_key, topic_key),
            ).fetchall()
            existing_ids = sorted(int(row["id"]) for row in existing_rows)
            if existing_rows and existing_rows[0]["content_hash"] == content_hash:
                return _memory_from_row(existing_rows[0])
            if existing_ids != expected_ids:
                raise ValueError(
                    "保存前必须确认当前冲突记忆，预期替代 {}，实际为 {}".format(
                        expected_ids, existing_ids
                    )
                )
            if expected_ids:
                lineage_id = existing_rows[0]["lineage_id"]
                version = max(int(row["version"]) for row in existing_rows) + 1
                supersedes_id = existing_rows[0]["id"]
                for row in existing_rows:
                    connection.execute(
                        "UPDATE memory_items SET status = 'superseded', updated_at = ? WHERE id = ?",
                        (timestamp, row["id"]),
                    )
            else:
                lineage_id = str(uuid.uuid4())
                version = 1
                supersedes_id = None
            connection.execute(
                """
                INSERT INTO memory_items
                    (memory_uuid, lineage_id, version, memory_type, scope_type,
                     scope_key, topic_key, content, structured_value_json,
                     source_json, tags_json, status, content_hash, review_due_at,
                     supersedes_id, created_at, updated_at, confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    lineage_id,
                    version,
                    memory_type,
                    scope_type,
                    scope_key,
                    topic_key,
                    normalized["content"],
                    json.dumps(normalized.get("structured_value"), ensure_ascii=False, sort_keys=True),
                    json.dumps(normalized["source"], ensure_ascii=False, sort_keys=True),
                    json.dumps(normalized.get("tags", []), ensure_ascii=False, sort_keys=True),
                    content_hash,
                    normalized.get("review_due_at"),
                    supersedes_id,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM memory_items WHERE rowid = last_insert_rowid()"
            ).fetchone()
        return _memory_from_row(row)

    def list_memories(
        self,
        include_inactive: bool = False,
        memory_type: Optional[str] = None,
        scope_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if not include_inactive:
            clauses.append("status = 'active'")
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        query = "SELECT * FROM memory_items"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_memory_from_row(row) for row in rows]

    def search_memories(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("搜索内容不能为空")
        limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT memory_items.* FROM memory_items
                    JOIN memory_items_fts ON memory_items_fts.rowid = memory_items.id
                    WHERE memory_items.status = 'active'
                      AND memory_items_fts MATCH ?
                    ORDER BY bm25(memory_items_fts), memory_items.updated_at DESC
                    LIMIT ?
                    """,
                    (query.strip(), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                pattern = "%{}%".format(query.strip())
                rows = connection.execute(
                    """
                    SELECT * FROM memory_items
                    WHERE status = 'active' AND (content LIKE ? OR topic_key LIKE ?)
                    ORDER BY updated_at DESC, id DESC LIMIT ?
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
        return [_memory_from_row(row) for row in rows]

    def get_memory_context(
        self,
        scope: Optional[Dict[str, Any]] = None,
        query: Optional[str] = None,
        max_items: int = 20,
        max_bytes: int = 32768,
    ) -> Dict[str, Any]:
        from mcp_server.services.memory import is_review_due

        scope = scope or {}
        strategy_id = scope.get("strategy_id")
        instrument_keys = set()
        for raw_code in scope.get("instruments", []) or []:
            code, market = normalize_ticker(str(raw_code))
            instrument_keys.add("{}{}".format(market, code))
        records = self.list_memories()
        if query:
            searchable = {item["memory_id"] for item in self.search_memories(query, 100)}
            records = [item for item in records if item["memory_id"] in searchable]
        eligible = []
        for item in records:
            if item["scope_type"] == "global":
                item_priority = 1
            elif item["scope_type"] == "strategy" and item["scope_key"] == strategy_id:
                item_priority = 2
            elif item["scope_type"] == "instrument" and item["scope_key"] in instrument_keys:
                item_priority = 3
            else:
                continue
            eligible.append((item_priority, item))
        eligible.sort(key=lambda pair: (-pair[0], pair[1]["updated_at"], pair[1]["memory_id"]))
        chosen = {}
        shadowed = []
        review_due = []
        for item_priority, item in eligible:
            topic = item["topic_key"]
            if topic in chosen:
                shadowed.append(item)
                continue
            chosen[topic] = item
            if is_review_due(item.get("review_due_at")):
                review_due.append(item)
            else:
                item["scope_priority"] = item_priority
        selected = [item for item in chosen.values() if item not in review_due]
        selected.sort(key=lambda item: (-item.get("scope_priority", 0), item["memory_id"]))
        selected = selected[: max(1, min(int(max_items), 100))]
        payload = {
            "memories": selected,
            "review_due": review_due,
            "shadowed": shadowed,
            "scope": {
                "strategy_id": strategy_id,
                "instruments": sorted(instrument_keys),
            },
        }
        while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > max_bytes and payload["memories"]:
            payload["memories"].pop()
        payload["context_hash"] = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return payload

    def archive_memory(self, memory_id: int, user_confirmed: bool) -> Dict[str, Any]:
        if not user_confirmed:
            raise ValueError("归档长期记忆前必须获得用户明确确认")
        with self._connect() as connection:
            connection.execute(
                "UPDATE memory_items SET status = 'archived', updated_at = ? WHERE id = ? AND status = 'active'",
                (_utc_now(), int(memory_id)),
            )
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?", (int(memory_id),)
            ).fetchone()
        if row is None:
            raise ValueError("找不到长期记忆：{}".format(memory_id))
        return _memory_from_row(row)

    def forget_memory(self, memory_id: int, user_confirmed: bool) -> Dict[str, Any]:
        if not user_confirmed:
            raise ValueError("永久删除长期记忆前必须获得用户明确确认")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?", (int(memory_id),)
            ).fetchone()
            if row is None:
                raise ValueError("找不到长期记忆：{}".format(memory_id))
            memory = _memory_from_row(row)
            analysis_references = []
            for analysis_row in connection.execute(
                "SELECT run_id, version, analysis_json FROM backtest_analyses"
            ).fetchall():
                try:
                    analysis = json.loads(analysis_row["analysis_json"])
                except (TypeError, ValueError):
                    continue
                if memory["memory_ref"] in (analysis.get("memory_refs") or []):
                    analysis_references.append(
                        {
                            "run_id": int(analysis_row["run_id"]),
                            "version": int(analysis_row["version"]),
                        }
                    )
            connection.execute(
                "UPDATE memory_items SET supersedes_id = NULL WHERE supersedes_id = ?",
                (int(memory_id),),
            )
            connection.execute("DELETE FROM memory_items WHERE id = ?", (int(memory_id),))
        return {
            "memory_id": int(memory_id),
            "deleted": True,
            "historical_references": memory.get("source", {}).get("run_ids", []),
            "analysis_references": analysis_references,
        }

    def export_memories(self, output_path: Union[str, Path]) -> Dict[str, Any]:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        memories = self.list_memories(include_inactive=True)
        payload = {
            "schema_version": 1,
            "exported_at": _utc_now(),
            "memories": memories,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), "count": len(memories), "schema_version": 1}

    def preview_memory_import(self, input_path: Union[str, Path]) -> Dict[str, Any]:
        from mcp_server.services.memory import memory_conflict_key, normalize_memory_candidate

        payload = _read_memory_export(input_path)
        import_hash = _memory_import_hash(payload)
        current_by_uuid = {
            item["memory_uuid"]: item for item in self.list_memories(include_inactive=True)
        }
        active_by_key = {
            memory_conflict_key(item): item for item in self.list_memories()
        }
        additions = []
        unchanged = []
        conflicts = []
        for item in payload["memories"]:
            memory_uuid = item.get("memory_uuid")
            if memory_uuid in current_by_uuid:
                if current_by_uuid[memory_uuid]["content_hash"] == item.get("content_hash"):
                    unchanged.append(memory_uuid)
                else:
                    conflicts.append(item)
                continue
            candidate = normalize_memory_candidate(item.get("candidate") or item)
            conflict = active_by_key.get(memory_conflict_key(candidate))
            if item.get("status", "active") == "active" and conflict is not None:
                conflict_item = dict(item)
                conflict_item["conflict_memory_id"] = conflict["memory_id"]
                conflict_item["conflict_reason"] = "active_topic_scope"
                conflicts.append(conflict_item)
            else:
                additions.append(item)
                if item.get("status", "active") == "active":
                    active_by_key[memory_conflict_key(candidate)] = item
        return {
            "schema_version": payload["schema_version"],
            "import_hash": import_hash,
            "additions": additions,
            "unchanged": unchanged,
            "conflicts": conflicts,
        }

    def import_memories(
        self,
        input_path: Union[str, Path],
        import_hash: str,
        user_confirmed: bool,
    ) -> Dict[str, Any]:
        if not user_confirmed:
            raise ValueError("导入长期记忆前必须获得用户明确确认")
        payload = _read_memory_export(input_path)
        if import_hash != _memory_import_hash(payload):
            raise ValueError("导入文件已变化，审批哈希失效")
        preview = self.preview_memory_import(input_path)
        if preview["conflicts"]:
            raise ValueError("导入存在已存在的记忆冲突，请先处理冲突")
        inserted = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in payload["memories"]:
                if item["memory_uuid"] in preview["unchanged"]:
                    continue
                candidate = item.get("candidate") or item
                from mcp_server.services.memory import normalize_memory_candidate

                normalized = normalize_memory_candidate(candidate)
                supersedes_id = None
                connection.execute(
                    """
                    INSERT INTO memory_items
                        (memory_uuid, lineage_id, version, memory_type, scope_type,
                         scope_key, topic_key, content, structured_value_json,
                         source_json, tags_json, status, content_hash, review_due_at,
                         supersedes_id, created_at, updated_at, confirmed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["memory_uuid"],
                        item.get("lineage_id") or str(uuid.uuid4()),
                        int(item.get("version", 1)),
                        normalized["memory_type"],
                        normalized["scope_type"],
                        normalized["scope_key"],
                        normalized["topic_key"],
                        normalized["content"],
                        json.dumps(normalized.get("structured_value"), ensure_ascii=False, sort_keys=True),
                        json.dumps(normalized["source"], ensure_ascii=False, sort_keys=True),
                        json.dumps(normalized.get("tags", []), ensure_ascii=False, sort_keys=True),
                        item.get("status", "active"),
                        item["content_hash"],
                        normalized.get("review_due_at"),
                        supersedes_id,
                        item.get("created_at") or _utc_now(),
                        item.get("updated_at") or _utc_now(),
                        item.get("confirmed_at") or item.get("created_at") or _utc_now(),
                    ),
                )
                inserted += 1
        return {"imported": inserted, "unchanged": len(preview["unchanged"])}

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


def _research_run_from_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    value = dict(row)
    value["snapshot"] = json.loads(value.pop("snapshot_json"))
    return value


def _sentiment_source_from_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    value = dict(row)
    value["enabled"] = bool(value["enabled"])
    value["config"] = json.loads(value.pop("config_json") or "{}")
    return value


def _sentiment_document_from_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    value = dict(row)
    value["targets"] = json.loads(value.pop("targets_json") or "[]")
    value["shenwan_industries"] = json.loads(value.pop("industries_json") or "[]")
    value["concept_tags"] = json.loads(value.pop("concepts_json") or "[]")
    value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
    return value


def _sentiment_extraction_from_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    value = dict(row)
    value["extraction"] = json.loads(value.pop("extraction_json"))
    return value


def _sentiment_snapshot_from_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    value = dict(row)
    value["snapshot"] = json.loads(value.pop("snapshot_json"))
    return value


def _sentiment_evidence_refs(extraction: Dict[str, Any]) -> List[str]:
    refs = [str(item) for item in extraction.get("evidence_refs", []) or []]
    for claim in extraction.get("claims", []) or []:
        if isinstance(claim, dict):
            refs.extend(str(item) for item in claim.get("evidence_refs", []) or [])
            if claim.get("evidence_id"):
                refs.append(str(claim["evidence_id"]))
    return refs


def _research_evidence_refs(analysis: Any) -> List[str]:
    refs: List[str] = []
    if isinstance(analysis, dict):
        direct = analysis.get("evidence_refs")
        if isinstance(direct, list):
            refs.extend(str(item) for item in direct)
        for value in analysis.values():
            refs.extend(_research_evidence_refs(value))
    elif isinstance(analysis, list):
        for value in analysis:
            refs.extend(_research_evidence_refs(value))
    return refs


def _memory_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "memory_id": int(row["id"]),
        "memory_uuid": row["memory_uuid"],
        "memory_ref": "memory:{}:v{}".format(row["id"], row["version"]),
        "lineage_id": row["lineage_id"],
        "version": int(row["version"]),
        "memory_type": row["memory_type"],
        "scope_type": row["scope_type"],
        "scope_key": row["scope_key"],
        "topic_key": row["topic_key"],
        "content": row["content"],
        "structured_value": json.loads(row["structured_value_json"])
        if row["structured_value_json"]
        else None,
        "source": json.loads(row["source_json"]),
        "tags": json.loads(row["tags_json"] or "[]"),
        "status": row["status"],
        "content_hash": row["content_hash"],
        "review_due_at": row["review_due_at"],
        "supersedes_id": row["supersedes_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "confirmed_at": row["confirmed_at"],
    }


def _ensure_memory_fts(connection: sqlite3.Connection) -> None:
    try:
        existing = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memory_items_fts'"
        ).fetchone()
        if existing is not None and "tags_json" not in (existing[0] or ""):
            connection.executescript(
                """
                DROP TRIGGER IF EXISTS memory_items_ai;
                DROP TRIGGER IF EXISTS memory_items_ad;
                DROP TRIGGER IF EXISTS memory_items_au;
                DROP TABLE IF EXISTS memory_items_fts;
                """
            )
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
                content, topic_key, tags_json, content='memory_items', content_rowid='id'
            )
            """
        )
        connection.executescript(
            """
            DROP TRIGGER IF EXISTS memory_items_ai;
            DROP TRIGGER IF EXISTS memory_items_ad;
            DROP TRIGGER IF EXISTS memory_items_au;
            CREATE TRIGGER IF NOT EXISTS memory_items_ai AFTER INSERT ON memory_items BEGIN
                INSERT INTO memory_items_fts(rowid, content, topic_key, tags_json)
                VALUES (new.id, new.content, new.topic_key, new.tags_json);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_items_ad AFTER DELETE ON memory_items BEGIN
                INSERT INTO memory_items_fts(memory_items_fts, rowid, content, topic_key, tags_json)
                VALUES ('delete', old.id, old.content, old.topic_key, old.tags_json);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_items_au AFTER UPDATE ON memory_items BEGIN
                INSERT INTO memory_items_fts(memory_items_fts, rowid, content, topic_key, tags_json)
                VALUES ('delete', old.id, old.content, old.topic_key, old.tags_json);
                INSERT INTO memory_items_fts(rowid, content, topic_key, tags_json)
                VALUES (new.id, new.content, new.topic_key, new.tags_json);
            END;
            """
        )
        connection.execute("INSERT INTO memory_items_fts(memory_items_fts) VALUES ('rebuild')")
    except sqlite3.OperationalError:
        # Structured memory remains usable on Python builds without FTS5.
        return


def _read_memory_export(input_path: Union[str, Path]) -> Dict[str, Any]:
    path = Path(input_path)
    if not path.exists():
        raise ValueError("找不到记忆导入文件：{}".format(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("记忆导入文件不是有效 UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("不支持的记忆导入格式")
    if not isinstance(payload.get("memories"), list):
        raise ValueError("记忆导入文件缺少 memories 数组")
    return payload


def _memory_import_hash(payload: Dict[str, Any]) -> str:
    content = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
