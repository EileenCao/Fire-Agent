from datetime import date

from mcp_server.adapters.a_stock_data import parse_tencent_quote_response
from mcp_server.adapters.a_stock_data import TencentMarketDataProvider
from mcp_server.domain.models import WatchlistItem


class _Response:
    content = b""

    def __init__(self, raw):
        self.content = raw.encode("gbk", errors="replace")

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, raw):
        self.raw = raw

    def get(self, url, timeout):
        return _Response(self.raw)


def test_tencent_parser_extracts_quote_fields_and_quote_time():
    values = [""] * 53
    values[1] = "红利ETF"
    values[3] = "1.234"
    values[4] = "1.200"
    values[30] = "20260810113000"
    values[31] = "0.034"
    values[32] = "2.83"
    values[37] = "12345.6"
    values[38] = "0.42"
    values[39] = "8.1"
    values[45] = "100.0"
    values[46] = "0.86"
    raw = 'v_sh512890="{}~{}";'.format("~".join(values), "")

    result = parse_tencent_quote_response(raw, {"sh512890": "512890"})

    assert result["512890"]["name"] == "红利ETF"
    assert result["512890"]["price"] == 1.234
    assert result["512890"]["change_pct"] == 2.83
    assert result["512890"]["pe_ttm"] == 8.1
    assert result["512890"]["quote_time"] == "20260810113000"


def test_provider_rejects_quote_after_morning_cutoff():
    values = [""] * 53
    values[1] = "红利ETF"
    values[3] = "1.234"
    values[4] = "1.200"
    values[30] = "20260810150000"
    raw = 'v_sh512890="{}~{}";'.format("~".join(values), "")
    provider = TencentMarketDataProvider(session=_Session(raw), skill=None)
    item = WatchlistItem(code="512890", market="SH", instrument_type="ETF")

    snapshot = provider.snapshots_for([item], "上午收盘 11:30")[0]

    assert snapshot.price is None
    assert "拒绝混入午间报告" in snapshot.errors[0]


def test_provider_rejects_quote_from_another_date_for_morning_report():
    values = [""] * 53
    values[1] = "红利ETF"
    values[3] = "1.234"
    values[4] = "1.200"
    values[30] = "20260811113000"
    raw = 'v_sh512890="{}~{}";'.format("~".join(values), "")
    provider = TencentMarketDataProvider(session=_Session(raw), skill=None)
    item = WatchlistItem(code="512890", market="SH", instrument_type="ETF")

    snapshot = provider.snapshots_for(
        [item], "上午收盘 11:30", report_date=date(2026, 8, 10)
    )[0]

    assert snapshot.price is None
    assert "日期" in snapshot.errors[0]
