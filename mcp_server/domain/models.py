"""Small, serializable domain objects shared by storage and services."""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import List, Optional


@dataclass(frozen=True)
class WatchlistItem:
    code: str
    market: str
    instrument_type: str
    name: Optional[str] = None
    note: str = ""
    enabled: bool = True
    item_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class DailyReportSchedule:
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    wake_time: time = time(12, 0)
    send_start: time = time(12, 3)
    send_end: time = time(12, 5)
    trading_days_only: bool = True


@dataclass(frozen=True)
class MarketSnapshot:
    code: str
    name: str
    instrument_type: str
    price: Optional[float]
    last_close: Optional[float]
    change_pct: Optional[float]
    amount_wan: Optional[float]
    turnover_pct: Optional[float]
    pe_ttm: Optional[float]
    pb: Optional[float]
    as_of: Optional[datetime]
    source_name: str
    source_url: Optional[str]
    status: str = "ok"
    signals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skill_name: Optional[str] = None
    skill_version: Optional[str] = None


@dataclass(frozen=True)
class DailyReport:
    report_date: date
    cutoff: str
    content: str
    data_as_of: Optional[datetime]
    status: str


@dataclass(frozen=True)
class RunResult:
    status: str
    report_id: Optional[int] = None
    message: str = ""
