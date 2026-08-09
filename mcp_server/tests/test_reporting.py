from datetime import date, datetime, timezone

from mcp_server.domain.models import MarketSnapshot
from mcp_server.services.reporting import DailyReportBuilder


def test_report_contains_morning_cutoff_source_and_valuation():
    snapshot = MarketSnapshot(
        code="512890",
        name="红利ETF",
        instrument_type="ETF",
        price=1.234,
        last_close=1.200,
        change_pct=2.83,
        amount_wan=12345.6,
        turnover_pct=0.42,
        pe_ttm=8.1,
        pb=0.86,
        as_of=datetime(2026, 8, 10, 11, 30, tzinfo=timezone.utc),
        source_name="腾讯财经",
        source_url="https://qt.gtimg.cn/",
        status="ok",
        skill_name="a-stock-data",
        skill_version="3.6.0",
    )

    report = DailyReportBuilder().build(
        report_date=date(2026, 8, 10),
        cutoff="上午收盘 11:30",
        snapshots=[snapshot],
    )

    assert "截至上午收盘 11:30" in report.content
    assert "512890" in report.content
    assert "红利ETF" in report.content
    assert "PE(TTM)" in report.content
    assert "腾讯财经" in report.content
    assert "a-stock-data" in report.content
    assert "3.6.0" in report.content
    assert report.data_as_of == snapshot.as_of


def test_report_marks_missing_data_instead_of_fabricating_values():
    snapshot = MarketSnapshot(
        code="600519",
        name="贵州茅台",
        instrument_type="STOCK",
        price=None,
        last_close=None,
        change_pct=None,
        amount_wan=None,
        turnover_pct=None,
        pe_ttm=None,
        pb=None,
        as_of=None,
        source_name="腾讯财经",
        source_url="https://qt.gtimg.cn/",
        status="partial",
        warnings=["行情接口暂时不可用"],
    )

    report = DailyReportBuilder().build(
        report_date=date(2026, 8, 10),
        cutoff="上午收盘 11:30",
        snapshots=[snapshot],
    )

    assert "数据缺失" in report.content
    assert "行情接口暂时不可用" in report.content
    assert report.status == "partial"
