from datetime import date

from mcp_server.calendar import TradingCalendar


def test_calendar_skips_weekends_and_configured_holidays():
    calendar = TradingCalendar(holidays={date(2026, 10, 1)})

    assert calendar.is_trading_day(date(2026, 8, 10)) is True
    assert calendar.is_trading_day(date(2026, 8, 9)) is False
    assert calendar.is_trading_day(date(2026, 10, 1)) is False


def test_calendar_exposes_authoritative_source(monkeypatch, tmp_path):
    monkeypatch.setattr("mcp_server.calendar._load_xshg_calendar", lambda: None)

    fallback = TradingCalendar()
    assert fallback.is_authoritative is False

    holiday_file = tmp_path / "trading_holidays.json"
    holiday_file.write_text('{"holidays": ["2026-10-01"]}', encoding="utf-8")
    configured = TradingCalendar(holiday_file=holiday_file)
    assert configured.is_authoritative is True
