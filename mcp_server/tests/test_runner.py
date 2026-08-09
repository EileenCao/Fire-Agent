from datetime import date

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
    )

    first = runner.run(date(2026, 8, 10))
    second = runner.run(date(2026, 8, 10))

    assert first.status == "sent"
    assert second.status == "already_sent"
    assert len(notifier.messages) == 1
    assert len(provider.calls) == 1
