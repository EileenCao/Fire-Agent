from datetime import date, datetime, timedelta

from mcp_server.calendar import TradingCalendar
from mcp_server.domain.models import MarketSnapshot
from mcp_server.services.runner import DailyReportRunner
from mcp_server.storage import SQLiteStore


class FakeMarketProvider:
    def __init__(self):
        self.calls = []

    def snapshots_for(self, items, cutoff):
        self.calls.append((items, cutoff))
        return [
            MarketSnapshot(
                code=item.code,
                name="测试ETF",
                instrument_type=item.instrument_type,
                price=1.0,
                last_close=1.0,
                change_pct=0.0,
                amount_wan=1.0,
                turnover_pct=0.1,
                pe_ttm=10.0,
                pb=1.0,
                as_of=None,
                source_name="fixture",
                source_url=None,
                status="partial",
                warnings=["fixture"],
            )
            for item in items
        ]


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, content):
        self.messages.append(content)
        return True


class FakeClock:
    def __init__(self, value):
        self.value = value
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


def test_runner_skips_non_trading_days_without_sending(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    store.add_watchlist_item("512890", instrument_type="ETF")
    notifier = FakeNotifier()
    runner = DailyReportRunner(
        store=store,
        market_provider=FakeMarketProvider(),
        notifier=notifier,
        calendar=TradingCalendar(),
    )

    result = runner.run(date(2026, 8, 9))

    assert result.status == "skipped_non_trading_day"
    assert notifier.messages == []


def test_runner_is_idempotent_for_same_trading_day(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    store.add_watchlist_item("512890", instrument_type="ETF")
    notifier = FakeNotifier()
    provider = FakeMarketProvider()
    runner = DailyReportRunner(
        store=store,
        market_provider=provider,
        notifier=notifier,
        calendar=TradingCalendar(),
        now_fn=lambda: datetime(2026, 8, 10, 12, 3),
    )

    first = runner.run(date(2026, 8, 10))
    second = runner.run(date(2026, 8, 10))

    assert first.status == "sent"
    assert second.status == "already_sent"
    assert len(notifier.messages) == 1
    assert len(provider.calls) == 1


def test_runner_waits_for_send_window_and_records_report_attempt(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    store.add_watchlist_item("512890", instrument_type="ETF")
    store.configure_daily_report()
    notifier = FakeNotifier()
    clock = FakeClock(datetime(2026, 8, 10, 12, 0))
    runner = DailyReportRunner(
        store=store,
        market_provider=FakeMarketProvider(),
        notifier=notifier,
        calendar=TradingCalendar(),
        now_fn=clock.now,
        sleep_fn=clock.sleep,
    )

    result = runner.run(date(2026, 8, 10), send=True)

    assert result.status == "sent"
    assert clock.sleeps
    assert clock.value.time().hour == 12
    assert clock.value.time().minute >= 3
    status = store.notification_status()
    assert status["latest_delivery"]["chunk_index"] == 0
    assert status["latest_delivery"]["chunk_count"] == 1


def test_runner_reports_missing_notifier_as_delivery_failure(tmp_path):
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    store.add_watchlist_item("512890", instrument_type="ETF")
    runner = DailyReportRunner(
        store=store,
        market_provider=FakeMarketProvider(),
        notifier=None,
        calendar=TradingCalendar(),
        now_fn=lambda: datetime(2026, 8, 10, 12, 3),
    )

    result = runner.run(date(2026, 8, 10), send=True)

    assert result.status == "notification_not_configured"
    assert store.get_report_run(
        "daily_watchlist:2026-08-10:morning_close:{}".format(store.watchlist_version())
    )["status"] == "delivery_failed"


def test_runner_blocks_scheduled_send_without_authoritative_calendar(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_server.calendar._load_xshg_calendar", lambda: None)
    store = SQLiteStore(tmp_path / "research.sqlite3")
    store.initialize()
    store.add_watchlist_item("512890", instrument_type="ETF")
    runner = DailyReportRunner(
        store=store,
        market_provider=FakeMarketProvider(),
        notifier=FakeNotifier(),
        calendar=TradingCalendar(),
        require_authoritative_calendar=True,
        now_fn=lambda: datetime(2026, 8, 10, 12, 3),
    )

    result = runner.run(date(2026, 8, 10), send=True)

    assert result.status == "blocked_calendar_unavailable"
