from datetime import date

from mcp_server.calendar import TradingCalendar


def test_calendar_skips_weekends_and_configured_holidays():
    calendar = TradingCalendar(holidays={date(2026, 10, 1)})

    assert calendar.is_trading_day(date(2026, 8, 10)) is True
    assert calendar.is_trading_day(date(2026, 8, 9)) is False
    assert calendar.is_trading_day(date(2026, 10, 1)) is False
