import json
from datetime import date

import pytest

from mcp_server.cli import main
from mcp_server.runtime import build_calendar, build_store
from mcp_server.workspace import WorkspaceError, initialize_workspace, load_workspace


def test_initialize_workspace_creates_user_data_tree_and_ignored_pointer(tmp_path):
    project_root = tmp_path / "FireAgent"
    project_root.mkdir()
    workspace_path = tmp_path / "FireAgentWorkspace"

    workspace = initialize_workspace(project_root, workspace_path)

    assert workspace.root == workspace_path.resolve()
    for path in (
        workspace.raw_dir,
        workspace.parquet_dir,
        workspace.strategy_dir,
        workspace.latest_artifacts_dir,
        workspace.formal_artifacts_dir,
        workspace.reports_dir,
        workspace.logs_dir,
    ):
        assert path.is_dir()
    assert workspace.db_path.parent == workspace.root
    pointer = project_root / ".fireagent" / "workspace.json"
    assert json.loads(pointer.read_text(encoding="utf-8"))["workspace_path"] == str(
        workspace.root
    )


def test_initialize_workspace_rejects_repository_and_descendant(tmp_path):
    project_root = tmp_path / "FireAgent"
    project_root.mkdir()

    with pytest.raises(WorkspaceError, match="独立工作区"):
        initialize_workspace(project_root, project_root)
    with pytest.raises(WorkspaceError, match="独立工作区"):
        initialize_workspace(project_root, project_root / "data")


def test_load_workspace_prefers_environment_override(tmp_path, monkeypatch):
    project_root = tmp_path / "FireAgent"
    project_root.mkdir()
    configured = initialize_workspace(project_root, tmp_path / "configured")
    overridden = initialize_workspace(project_root, tmp_path / "overridden", overwrite=True)
    monkeypatch.setenv("FIREAGENT_WORKSPACE", str(overridden.root))

    loaded = load_workspace(project_root)

    assert loaded.root == overridden.root
    assert loaded.root != configured.root


def test_cli_init_accepts_user_workspace_path(tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "FireAgent"
    project_root.mkdir()
    workspace_path = tmp_path / "FireAgentWorkspace"
    monkeypatch.chdir(project_root)

    assert main(["init", "--workspace", str(workspace_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert result["workspace"] == str(workspace_path.resolve())


def test_runtime_defaults_follow_the_independent_workspace(tmp_path, monkeypatch):
    project_root = tmp_path / "FireAgent"
    project_root.mkdir()
    workspace = initialize_workspace(project_root, tmp_path / "FireAgentWorkspace")
    workspace.calendar_path.write_text(
        json.dumps({"holidays": ["2026-01-01"]}), encoding="utf-8"
    )
    monkeypatch.chdir(project_root)

    store = build_store(project_root, require_workspace=True)
    calendar = build_calendar(project_root, require_workspace=True)

    assert store.path == workspace.db_path
    assert date(2026, 1, 1) in calendar.holidays
