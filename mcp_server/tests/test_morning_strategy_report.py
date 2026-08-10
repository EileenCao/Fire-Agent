import json
from datetime import date
from pathlib import Path

from mcp_server.domain.models import MarketSnapshot
from mcp_server.domain.strategy import StrategySpec
from mcp_server.services.reporting import DailyReportBuilder, write_daily_report
from mcp_server.services.morning_report import build_morning_strategy_signal_builder
from mcp_server.services.strategy_signal import MorningStrategySignalEvaluator


def _strategy():
    payload = json.loads(
        Path(r"D:\Life_lover\FIRE计划\FireAgentWorkspace\strategies\512890-core-rsi-profit-0.json")
        .read_text(encoding="utf-8")
    )
    return StrategySpec.from_dict(payload)


def _bars(count=130):
    return [
        {
            "date": date(2025, 1, 2).fromordinal(date(2025, 1, 2).toordinal() + index).isoformat(),
            "open": 100.0 - index,
            "high": 100.0 - index,
            "low": 100.0 - index,
            "close": 100.0 - index,
        }
        for index in range(count)
    ]


def test_morning_signal_appends_intraday_price_and_marks_approximation():
    result = MorningStrategySignalEvaluator().evaluate(
        _strategy(),
        _bars(),
        report_date=date(2026, 8, 10),
        morning_price=49.0,
        data_as_of="2026-08-10T11:30:00+08:00",
    )

    assert result["status"] == "ok"
    assert result["mode"] == "morning_close_approximation"
    assert result["signal"]["evidence"]["data_as_of"] == "2026-08-10"
    assert result["signal"]["evidence"]["morning_price"] == 49.0
    assert result["state"]["cash"] <= _strategy().initial_capital


def test_daily_report_renders_strategy_signal_section():
    snapshot = MarketSnapshot(
        code="512890",
        name="测试ETF",
        instrument_type="ETF",
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
    )
    signal = {
        "status": "ok",
        "action": "BUY",
        "mode": "morning_close_approximation",
        "strategy_id": "etf-512890-core-rsi-profit-0",
        "strategy_version": "1.3.0-profit0",
        "signal": {"buy_cash": 1000, "sell_quantity": 0, "evidence": {}},
        "state": {"cash": 49000, "total_quantity": 1000},
    }

    report = DailyReportBuilder().build(
        date(2026, 8, 10), "上午收盘 11:30", [snapshot], strategy_signals=[signal]
    )

    assert "## 策略信号" in report.content
    assert "BUY" in report.content
    assert "1.3.0-profit0" in report.content


def test_write_daily_report_persists_markdown_file(tmp_path):
    path = write_daily_report(
        tmp_path,
        date(2026, 8, 10),
        "# 测试日报\n",
    )

    assert path == tmp_path / "daily" / "2026-08-10.md"
    assert path.read_text(encoding="utf-8") == "# 测试日报\n"


class _HistoricalProvider:
    def fetch(self, codes, start_date, end_date):
        return type("Fetched", (), {"data": {"512890": _bars()}})()


def test_runtime_builder_uses_fixed_strategy_and_snapshot_price():
    snapshot = MarketSnapshot(
        code="512890",
        name="测试ETF",
        instrument_type="ETF",
        price=49.0,
        last_close=50.0,
        change_pct=-2.0,
        amount_wan=1.0,
        turnover_pct=0.1,
        pe_ttm=10.0,
        pb=1.0,
        as_of=None,
        source_name="fixture",
        source_url=None,
    )
    builder = build_morning_strategy_signal_builder(
        Path(r"D:\Life_lover\FIRE计划\FireAgentWorkspace\strategies\512890-core-rsi-profit-0.json"),
        _HistoricalProvider(),
    )

    result = builder([], [snapshot], date(2026, 8, 10))

    assert result[0]["strategy_version"] == "1.3.0-profit0"
    assert result[0]["signal"]["evidence"]["morning_price"] == 49.0
