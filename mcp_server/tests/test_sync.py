import json
from pathlib import Path

from mcp_server.cli import main


PROJECT_ROOT = Path(__file__).parents[2]
PROJECT_SKILLS = (
    "strategy-workbench",
    "backtest-analysis",
    "daily-strategy-observer",
)


def _write_skill(path, version="3.6.0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: a-stock-data\nversion: {}\n---\n".format(version),
        encoding="utf-8",
    )


def _write_project_skills(root):
    for name in PROJECT_SKILLS:
        path = root / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: {}\n---\n".format(name), encoding="utf-8")


def test_sync_generates_project_scoped_codex_config(tmp_path, monkeypatch, capsys):
    skill = tmp_path / "external" / "a-stock-data" / "SKILL.md"
    _write_skill(skill)
    _write_project_skills(tmp_path)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill))
    monkeypatch.chdir(tmp_path)
    workspace_path = tmp_path.parent / (tmp_path.name + "-FireAgentWorkspace")
    assert main(["init", "--workspace", str(workspace_path)]) == 0
    capsys.readouterr()

    assert main(["sync"]) == 0

    result = json.loads(capsys.readouterr().out)
    config = tmp_path / ".codex" / "config.toml"
    content = config.read_text(encoding="utf-8")
    assert result["status"] == "ok"
    assert result["config_path"] == str(config)
    assert result["a_stock_data_skill"]["version"] == "3.6.0"
    assert config.exists()
    assert "[mcp_servers.fireagent]" in content
    assert "mcp_server.server" in content
    assert json.dumps(str(tmp_path)) in content
    assert json.dumps(str(skill)) in content
    assert "FIREAGENT_WORKSPACE" in content
    assert json.dumps(str(workspace_path)) in content
    assert json.dumps(str(workspace_path / "stock_research.sqlite3")) in content
    assert "FIREAGENT_ENABLE_FEISHU" not in content


def test_sync_fails_without_required_skill(tmp_path, monkeypatch, capsys):
    _write_project_skills(tmp_path)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(tmp_path / "missing" / "SKILL.md"))
    monkeypatch.chdir(tmp_path)

    assert main(["sync"]) == 2

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert "a-stock-data" in result["error"]
    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_sync_requires_all_project_skills(tmp_path, monkeypatch, capsys):
    skill = tmp_path / "external" / "a-stock-data" / "SKILL.md"
    _write_skill(skill)
    (tmp_path / "skills" / "strategy-workbench").mkdir(parents=True)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill))
    monkeypatch.chdir(tmp_path)
    workspace_path = tmp_path.parent / (tmp_path.name + "-FireAgentWorkspace")
    assert main(["init", "--workspace", str(workspace_path)]) == 0
    capsys.readouterr()

    assert main(["sync"]) == 2

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert "backtest-analysis" in result["error"]


def test_sync_requires_initialized_workspace(tmp_path, monkeypatch, capsys):
    skill = tmp_path / "external" / "a-stock-data" / "SKILL.md"
    _write_skill(skill)
    _write_project_skills(tmp_path)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill))
    monkeypatch.chdir(tmp_path)

    assert main(["sync"]) == 2

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert "工作区" in result["error"]


def test_local_sync_files_are_ignored_but_examples_are_not():
    ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "scripts/sync.ps1" in ignore
    assert "scripts/sync.sh" in ignore
    assert ".codex/config.toml" in ignore
    assert ".fireagent/workspace.json" in ignore
    assert "data/raw/" in ignore
    assert "data/parquet/" in ignore


def test_sync_examples_are_portable():
    powershell = (PROJECT_ROOT / "scripts" / "sync.ps1.example").read_text(
        encoding="utf-8"
    )
    shell = (PROJECT_ROOT / "scripts" / "sync.sh.example").read_text(
        encoding="utf-8"
    )

    assert "mcp_server.cli sync" in powershell
    assert "mcp_server.cli sync" in shell
    assert "C:\\Users\\Caoji" not in powershell
    assert "C:\\Users\\Caoji" not in shell
