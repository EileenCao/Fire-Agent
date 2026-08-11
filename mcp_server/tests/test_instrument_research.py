from datetime import date

import pytest

from mcp_server.services.research import InstrumentResearchService


class FixtureProvider:
    provider_id = "fixture"
    skill_name = "a-stock-data"
    skill_version = "3.6.0"

    def collect(self, instrument, sections, as_of=None, refresh=False):
        bars = []
        closes = [10.0, 10.5, 10.2, 10.8, 11.0, 11.4, 11.2, 11.8]
        for index, close in enumerate(closes, start=1):
            bars.append(
                {
                    "date": "2026-08-{0:02d}".format(index),
                    "open": close - 0.1,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 1000 + index,
                    "amount": (1000 + index) * close,
                }
            )
        return {
            "market": {
                "data": {
                    "name": "测试ETF",
                    "price": 11.8,
                    "last_close": 11.2,
                    "change_pct": 5.36,
                    "amount_wan": 12.5,
                    "turnover_pct": 1.2,
                    "pe_ttm": 8.0,
                    "pb": 1.1,
                    "quote_time": "2026-08-11 11:30:00",
                },
                "provenance": {
                    "source_name": "fixture quote",
                    "source_url": "test://quote",
                    "data_as_of": "2026-08-11T11:30:00+08:00",
                },
                "status": "ok",
            },
            "bars": {
                "data": bars,
                "provenance": {
                    "source_name": "fixture bars",
                    "source_url": "test://bars",
                    "data_as_of": "2026-08-08",
                },
                "status": "ok",
            },
            "fundamentals": {
                "data": {},
                "provenance": {},
                "status": "missing",
                "error_reason": "fixture does not provide fundamentals",
            },
            "valuation": {
                "data": {"pe_ttm": 8.0, "pb": 1.1},
                "provenance": {
                    "source_name": "fixture quote",
                    "source_url": "test://quote",
                    "data_as_of": "2026-08-11T11:30:00+08:00",
                },
                "status": "partial",
            },
            "news": {
                "data": [],
                "provenance": {},
                "status": "missing",
                "error_reason": "not requested",
            },
        }


def test_research_normalizes_512890_and_calculates_technical_score():
    service = InstrumentResearchService(FixtureProvider())

    result = service.build(
        code="512890",
        instrument_type=None,
        as_of=date(2026, 8, 11),
    )

    assert result["instrument"] == {
        "code": "512890",
        "market": "SH",
        "instrument_type": "ETF",
        "name": "测试ETF",
    }
    assert result["technical"]["status"] == "ok"
    assert result["technical"]["provenance"]["source_url"] == "test://bars"
    assert result["technical"]["indicators"]["ma5"] is not None
    assert "volume_ratio20" in result["technical"]["indicators"]
    assert result["technical"]["indicators"]["rsi14"] is None
    assert result["scores"]["status"] in {"insufficient_evidence", "watch", "continue_research"}
    assert result["scores"]["coverage"] < 1.0
    assert all(
        "weight" in dimension and "raw_values" in dimension
        for dimension in result["scores"]["dimensions"].values()
    )


def test_research_keeps_missing_sections_and_field_provenance():
    service = InstrumentResearchService(FixtureProvider())

    result = service.build(code="512890", instrument_type="ETF")

    assert result["sections"]["fundamentals"]["status"] == "missing"
    assert any(
        "fixture does not provide fundamentals" in warning
        for warning in result["warnings"]
    )
    assert result["market"]["provenance"]["source_url"] == "test://quote"
    assert result["provenance"]["provider_id"] == "fixture"
    assert result["provenance"]["skill_version"] == "3.6.0"
    assert result["evidence"]
    assert all(item["evidence_id"] for item in result["evidence"])


def test_research_rejects_unknown_instrument_type():
    service = InstrumentResearchService(FixtureProvider())

    with pytest.raises(ValueError, match="instrument_type"):
        service.build(code="512890", instrument_type="INDEX")
