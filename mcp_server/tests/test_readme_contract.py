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
