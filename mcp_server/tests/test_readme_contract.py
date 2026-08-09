from pathlib import Path


def test_readme_is_windows_first_and_declares_runtime_contract():
    readme = Path(__file__).parents[2] / "README.md"
    content = readme.read_text(encoding="utf-8")

    assert "a-stock-data" in content
    assert "A_STOCK_DATA_SKILL_PATH" in content
    assert "doctor" in content
    assert "validate-strategy" in content
    assert "run-backtest" in content
    assert "不自动下单" in content
    assert "不需要 Ollama" in content
    assert "飞书" in content
    assert "PowerShell" in content


def test_readme_includes_mcp_connection_smoke_test():
    readme = Path(__file__).parents[2] / "README.md"
    content = readme.read_text(encoding="utf-8")

    assert "tools/list" in content
    assert "不运行回测" in content
    assert "validate_strategy" in content
    assert "get_notification_status" in content


def test_readme_explains_agent_assisted_installation():
    readme = Path(__file__).parents[2] / "README.md"
    content = readme.read_text(encoding="utf-8")

    assert "https://github.com/EileenCao/Fire-Agent.git" in content
    assert "Codex" in content
    assert "WorkBuddy" in content
    assert "Claude Code" in content
    assert "安装提示词" in content


def test_readme_explains_user_workspace_and_automatic_data():
    readme = Path(__file__).parents[2] / "README.md"
    content = readme.read_text(encoding="utf-8")

    assert "init --workspace" in content
    assert "不在 FireAgent 仓库内" in content
    assert "不执行 Git 同步" in content
    assert "自动通过 `a-stock-data` 准备历史日线" in content
    assert "`--data` 仍保留" in content


def test_readme_documents_feishu_scheduled_delivery():
    readme = Path(__file__).parents[2] / "README.md"
    content = readme.read_text(encoding="utf-8")

    assert "notification-test" in content
    assert "notification-status" in content
    assert "config/.env" in content
    assert "任务计划" in content
    assert "daily-report --send" in content
    assert "20 KB" in content
    assert "双向对话" in content
