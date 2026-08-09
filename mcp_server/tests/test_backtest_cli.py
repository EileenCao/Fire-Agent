import json

from mcp_server.cli import main
from mcp_server.services.historical_data import HistoricalDataResult


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
        "benchmark": None,
        "risk_free_rate_annual": 0.0,
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


def _init_workspace(tmp_path, capsys):
    workspace_path = tmp_path.parent / (tmp_path.name + "-FireAgentWorkspace")
    assert main(["init", "--workspace", str(workspace_path)]) == 0
    capsys.readouterr()
    return workspace_path


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
    _init_workspace(tmp_path, capsys)
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
        "--confirm-benchmark",
        "--confirm-risk-free-rate",
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["run_id"] > 0
    artifact_dir = __import__("pathlib").Path(result["artifacts"]["artifact_dir"])
    assert artifact_dir.parent == output_dir
    assert (artifact_dir / "result.json").exists()
    assert (artifact_dir / "report.md").exists()
    assert (artifact_dir / "trades.csv").exists()


def test_cli_uses_default_strategy_path_when_strategy_is_omitted(tmp_path, monkeypatch, capsys):
    skill_path = tmp_path / "skills" / "a-stock-data" / "SKILL.md"
    _write_skill(skill_path)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill_path))
    monkeypatch.chdir(tmp_path)
    workspace_path = _init_workspace(tmp_path, capsys)
    strategy_path, data_path = _write_inputs(tmp_path)
    default_strategy_path = workspace_path / "strategies" / "strategy.json"
    default_strategy_path.write_text(strategy_path.read_text(encoding="utf-8"), encoding="utf-8")
    output_dir = tmp_path / "artifacts"

    assert main([
        "run-backtest",
        "--data",
        str(data_path),
        "--output-dir",
        str(output_dir),
        "--confirm-benchmark",
        "--confirm-risk-free-rate",
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["run_id"] > 0


def test_cli_uses_latest_artifacts_path_when_output_dir_is_omitted(tmp_path, monkeypatch, capsys):
    skill_path = tmp_path / "skills" / "a-stock-data" / "SKILL.md"
    _write_skill(skill_path)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill_path))
    monkeypatch.chdir(tmp_path)
    workspace_path = _init_workspace(tmp_path, capsys)
    strategy_path, data_path = _write_inputs(tmp_path)

    assert main([
        "run-backtest",
        "--strategy",
        str(strategy_path),
        "--data",
        str(data_path),
        "--confirm-benchmark",
        "--confirm-risk-free-rate",
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["run_id"] > 0
    output_dir = workspace_path / "artifacts" / "latest"
    artifact_dir = __import__("pathlib").Path(result["artifacts"]["artifact_dir"])
    assert artifact_dir.parent == output_dir
    assert (artifact_dir / "result.json").exists()
    assert (artifact_dir / "report.md").exists()
    assert (artifact_dir / "trades.csv").exists()


def test_cli_fetches_data_automatically_into_the_user_workspace(
    tmp_path, monkeypatch, capsys
):
    skill_path = tmp_path / "skills" / "a-stock-data" / "SKILL.md"
    _write_skill(skill_path)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill_path))
    monkeypatch.chdir(tmp_path)
    workspace_path = tmp_path.parent / (tmp_path.name + "-FireAgentWorkspace")
    assert main(["init", "--workspace", str(workspace_path)]) == 0
    capsys.readouterr()

    strategy = _write_inputs(tmp_path)[0].read_text(encoding="utf-8")
    workspace_strategy = workspace_path / "strategies" / "strategy.json"
    workspace_strategy.write_text(strategy, encoding="utf-8")

    class FakeProvider:
        def fetch(self, codes, start_date, end_date):
            return HistoricalDataResult(
                data={
                    "512890": [
                        {"date": "2026-01-01", "open": 10, "high": 10, "low": 10, "close": 10},
                        {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
                        {"date": "2026-01-03", "open": 10, "high": 10, "low": 10, "close": 10},
                        {"date": "2026-01-04", "open": 10, "high": 12, "low": 10, "close": 12},
                        {"date": "2026-01-05", "open": 20, "high": 20, "low": 18, "close": 18},
                    ]
                },
                provenance={
                    "source_name": "fake-a-stock-data",
                    "source_url": "test://historical",
                    "source_version": "a-stock-data:3.6.0",
                    "skill_name": "a-stock-data",
                    "skill_version": "3.6.0",
                    "price_basis": "adjusted",
                },
            )

    monkeypatch.setattr("mcp_server.cli.build_historical_data_provider", lambda root: FakeProvider())

    assert main(["run-backtest", "--confirm-benchmark", "--confirm-risk-free-rate"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["result"]["provenance"]["source_name"] == "fake-a-stock-data"
    artifact_dir = __import__("pathlib").Path(result["artifacts"]["artifact_dir"])
    assert artifact_dir.parent == workspace_path / "artifacts" / "latest"
    assert (artifact_dir / "result.json").exists()
