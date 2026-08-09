import json

from mcp_server.cli import main


def _write_skill(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: a-stock-data\nversion: 3.6.0\n---\n", encoding="utf-8")


def _write_inputs(tmp_path):
    strategy = {
        "strategy_id": "ma-trend",
        "version": "1.0.0",
        "name": "均线趋势",
        "universe": ["512890"],
        "frequency": "1d",
        "entry": {"rules": [{"type": "cross_above", "left": "sma_2", "right": "sma_3"}]},
        "exit": {"rules": [{"type": "cross_below", "left": "sma_2", "right": "sma_3"}]},
        "position_sizing": {"type": "all_in"},
        "data_policy": {"source_name": "fixture", "source_version": "a-stock-data:3.6.0"},
    }
    data = {"512890": [
        {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10},
        {"date": "2026-01-04", "open": 10, "high": 12, "low": 10, "close": 12},
        {"date": "2026-01-05", "open": 20, "high": 20, "low": 18, "close": 18},
    ]}
    strategy_path = tmp_path / "strategy.json"
    data_path = tmp_path / "data.json"
    strategy_path.write_text(json.dumps(strategy, ensure_ascii=False), encoding="utf-8")
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return strategy_path, data_path


def test_cli_validates_strategy_file(tmp_path, monkeypatch, capsys):
    strategy_path, _ = _write_inputs(tmp_path)

    assert main(["validate-strategy", "--file", str(strategy_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is True


def test_cli_runs_backtest_and_writes_local_artifacts(tmp_path, monkeypatch, capsys):
    skill_path = tmp_path / "skills" / "a-stock-data" / "SKILL.md"
    _write_skill(skill_path)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill_path))
    monkeypatch.chdir(tmp_path)
    strategy_path, data_path = _write_inputs(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main([
        "run-backtest",
        "--strategy",
        str(strategy_path),
        "--data",
        str(data_path),
        "--output-dir",
        str(output_dir),
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["run_id"] > 0
    assert (output_dir / "result.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trades.csv").exists()
