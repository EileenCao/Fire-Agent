import json

import pytest

from mcp_server.cli import main
from mcp_server.dependencies import AStockDataSkillError
from mcp_server.runtime import build_market_provider


def _write_skill(path, version="3.6.0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: a-stock-data\nversion: {}\n---\n".format(version),
        encoding="utf-8",
    )


def test_cli_can_configure_and_list_a_local_watchlist(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    workspace_path = tmp_path.parent / (tmp_path.name + "-FireAgentWorkspace")
    assert main(["init", "--workspace", str(workspace_path)]) == 0
    capsys.readouterr()

    assert main(["watchlist-add", "512890", "--instrument-type", "ETF"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "512890"

    assert main(["configure", "--wake-time", "12:00"]) == 0
    configured = json.loads(capsys.readouterr().out)
    assert configured["wake_time"] == "12:00:00"
    assert main(["watchlist-list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["instrument_type"] == "ETF"


def test_doctor_reports_a_stock_data_skill(tmp_path, monkeypatch, capsys):
    skill_path = tmp_path / "skills" / "a-stock-data" / "SKILL.md"
    _write_skill(skill_path)
    monkeypatch.setenv("A_STOCK_DATA_SKILL_PATH", str(skill_path))
    monkeypatch.chdir(tmp_path)

    assert main(["doctor"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["a_stock_data_skill"]["name"] == "a-stock-data"
    assert result["a_stock_data_skill"]["version"] == "3.6.0"
    assert result["a_stock_data_skill"]["path"] == str(skill_path)


def test_real_market_provider_fails_when_required_skill_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "A_STOCK_DATA_SKILL_PATH", str(tmp_path / "missing" / "SKILL.md")
    )

    with pytest.raises(AStockDataSkillError, match="a-stock-data.*安装"):
        build_market_provider()


def test_cli_notification_status_reports_configuration_without_sending(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    workspace_path = tmp_path.parent / (tmp_path.name + "-FireAgentWorkspace")
    assert main(["init", "--workspace", str(workspace_path)]) == 0
    capsys.readouterr()
    (workspace_path / "config" / ".env").write_text(
        "FIREAGENT_ENABLE_FEISHU=1\n"
        "FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/test\n",
        encoding="utf-8",
    )
    for key in ("FIREAGENT_ENABLE_FEISHU", "FEISHU_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)

    assert main(["notification-status"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["webhook_configured"] is True
    assert result["network_send_performed"] is False
