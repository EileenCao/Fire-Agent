import json

from mcp_server.cli import main


class TinyProvider:
    provider_id = "fixture"
    skill_name = "a-stock-data"
    skill_version = "3.6.0"

    def collect(self, instrument, sections, as_of=None, refresh=False):
        return {
            "market": {
                "data": {"name": "CLI ETF", "price": 1.2},
                "provenance": {"source_name": "fixture", "source_url": "test://quote"},
                "status": "ok",
            },
            "bars": {
                "data": [
                    {"date": "2026-08-10", "open": 1, "high": 2, "low": 1, "close": 1.1},
                    {"date": "2026-08-11", "open": 1.1, "high": 2, "low": 1, "close": 1.2},
                ],
                "provenance": {"source_name": "fixture", "source_url": "test://bars"},
                "status": "ok",
            },
        }


def _write_skill(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: a-stock-data\nversion: 3.6.0\n---\n", encoding="utf-8")


def test_research_cli_uses_workspace_artifacts_and_lists_snapshots(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    skill = tmp_path / "external" / "a-stock-data" / "SKILL.md"
    _write_skill(skill)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill))
    workspace = tmp_path.parent / (tmp_path.name + "-FireAgentWorkspace")
    assert main(["init", "--workspace", str(workspace)]) == 0
    capsys.readouterr()

    from mcp_server import cli

    monkeypatch.setattr(cli, "build_instrument_research_provider", lambda root: TinyProvider())
    assert main(["research", "512890"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["snapshot_id"] == 1
    assert created["artifacts"]["artifact_dir"].startswith(str(workspace / "artifacts"))

    assert main(["research-list", "--code", "512890"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["code"] == "512890"
