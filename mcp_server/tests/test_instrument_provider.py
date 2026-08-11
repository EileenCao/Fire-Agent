from datetime import datetime

from mcp_server.adapters.instrument_research import AStockDataInstrumentProvider
from mcp_server.domain.models import MarketSnapshot
from mcp_server.services.historical_data import HistoricalDataResult
from mcp_server.services.provider_registry import ProviderRegistry


class FakeMarketProvider:
    skill = type("Skill", (), {"name": "a-stock-data", "version": "3.6.0"})()

    def snapshots_for(self, items, cutoff, report_date=None):
        item = list(items)[0]
        return [
            MarketSnapshot(
                code=item.code,
                name="测试ETF",
                instrument_type=item.instrument_type,
                price=1.2,
                last_close=1.1,
                change_pct=9.09,
                amount_wan=20,
                turnover_pct=1.0,
                pe_ttm=8.0,
                pb=1.1,
                as_of=datetime.fromisoformat("2026-08-11T11:30:00+08:00"),
                source_name="fixture quote",
                source_url="test://quote",
                skill_name="a-stock-data",
                skill_version="3.6.0",
            )
        ]


class FakeHistoricalProvider:
    def fetch(self, codes, start_date, end_date):
        return HistoricalDataResult(
            data={"512890": [{"date": "2026-08-10", "close": 1.2}]},
            provenance={
                "source_name": "fixture bars",
                "source_url": "test://bars",
                "source_version": "a-stock-data:3.6.0",
                "skill_name": "a-stock-data",
                "skill_version": "3.6.0",
                "data_start": "2026-08-10",
                "data_end": "2026-08-10",
            },
        )


def test_a_stock_provider_combines_existing_market_and_history_adapters():
    provider = AStockDataInstrumentProvider(
        market_provider=FakeMarketProvider(),
        historical_data_provider=FakeHistoricalProvider(),
        section_fetchers={
            "news": lambda instrument, as_of=None: {
                "data": [{"title": "fixture news"}],
                "provenance": {"source_name": "fixture news", "source_url": "test://news"},
                "status": "ok",
            }
        },
    )

    result = provider.collect(
        {"code": "512890", "market": "SH", "instrument_type": "ETF"},
        ["market", "bars", "news", "fundamentals"],
    )

    assert result["market"]["data"]["price"] == 1.2
    assert result["bars"]["data"][0]["date"] == "2026-08-10"
    assert result["news"]["data"][0]["title"] == "fixture news"
    assert result["fundamentals"]["status"] == "missing"
    assert "tracking_index" in result["fundamentals"]["data"]
    assert "premium_discount" in result["fundamentals"]["data"]
    assert result["market"]["provenance"]["skill_version"] == "3.6.0"


def test_provider_registry_requires_explicit_provider_id():
    provider = object()
    registry = ProviderRegistry({"a-stock-data": provider})

    assert registry.get("a-stock-data") is provider
    assert registry.ids() == ["a-stock-data"]
    try:
        registry.get("tushare")
    except ValueError as exc:
        assert "provider_id" in str(exc)
    else:
        raise AssertionError("unknown provider should be rejected")
