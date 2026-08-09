import json
from pathlib import Path

from mcp_server.services.artifacts import write_backtest_artifacts


def _result():
    return {
        "strategy_id": "etf/512890:rsi timing",
        "strategy_version": "1.0.0",
        "run_mode": "formal",
        "provenance": {
            "source_name": "fixture",
            "source_version": "a-stock-data:3.6.0",
            "skill_name": "a-stock-data",
            "skill_version": "3.6.0",
            "data_start": "2024-01-01",
            "data_end": "2024-03-29",
            "frequency": "1d",
        },
        "scenarios": {
            "default": {
                "equity_curve": {
                    "2024-01-01": 1000,
                    "2024-01-02": 1010,
                    "2024-02-01": 990,
                    "2024-03-29": 1100,
                },
                "cash_curve": {"2024-01-01": 1000, "2024-03-29": 500},
                "market_value_curve": {"2024-01-01": 0, "2024-03-29": 600},
                "exposure_curve": {"2024-01-01": 0, "2024-03-29": 0.545},
                "trades": [
                    {
                        "code": "512890",
                        "side": "SELL",
                        "date": "2024-03-29",
                        "signal_date": "2024-03-28",
                        "price": 1.1,
                        "quantity": 100,
                        "status": "FILLED",
                        "pnl": 100,
                        "fee": 1,
                        "reason": "EXIT_RULE",
                    }
                ],
                "cash_flows": [],
                "positions": {},
                "metrics": {
                    "initial_capital": 1000,
                    "final_equity": 1100,
                    "cumulative_return": 0.1,
                    "time_weighted_return": 0.1,
                    "annualized_return": 0.4,
                    "annualized_volatility": 0.2,
                    "max_drawdown": 0.02,
                    "trade_count": 1,
                    "win_rate": 1.0,
                },
                "warnings": ["512890 2024-02-01 没有可卖持仓"],
            }
        },
        "validation": {},
        "warnings": ["512890 2024-02-01 没有可卖持仓"],
    }


def test_write_backtest_artifacts_creates_unique_run_directory_and_charts(tmp_path):
    first = write_backtest_artifacts(
        tmp_path,
        _result(),
        run_id=2,
        created_at="2026-08-09T11:54:34+00:00",
    )
    second = write_backtest_artifacts(
        tmp_path,
        _result(),
        run_id=3,
        created_at="2026-08-09T11:54:34+00:00",
    )

    assert first["artifact_dir"] != second["artifact_dir"]
    artifact_dir = Path(first["artifact_dir"])
    assert artifact_dir.name == "20260809-195434_etf-512890-rsi-timing_v1.0.0_run-2"
    assert Path(first["report"]).parent == artifact_dir
    assert Path(first["result"]).exists()
    assert Path(first["trades"]).exists()
    assert Path(first["analysis"]).exists()
    assert Path(first["charts"]).is_dir()
    assert Path(first["charts"]).joinpath("equity_drawdown.png").exists()
    assert "年化收益率" in Path(first["report"]).read_text(encoding="utf-8")


def test_report_keeps_deterministic_result_separate_from_ai_analysis(tmp_path):
    artifacts = write_backtest_artifacts(tmp_path, _result(), run_id=9)

    result_payload = json.loads(Path(artifacts["result"]).read_text(encoding="utf-8"))
    analysis_payload = json.loads(Path(artifacts["analysis"]).read_text(encoding="utf-8"))

    assert result_payload["run_id"] == 9
    assert analysis_payload["status"] == "pending"
    assert "AI 观察" in Path(artifacts["report"]).read_text(encoding="utf-8")
