import pytest

from mcp_server.dependencies import (
    AStockDataSkillError,
    discover_a_stock_data_skill,
    require_a_stock_data_skill,
)


def _write_skill(path, name="a-stock-data", version="3.6.0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "name: {}\n"
        "version: {}\n"
        "---\n\n# skill\n".format(name, version),
        encoding="utf-8",
    )


def test_discover_reads_skill_name_and_version_from_frontmatter(tmp_path):
    skill_path = tmp_path / "skills" / "a-stock-data" / "SKILL.md"
    _write_skill(skill_path)

    result = discover_a_stock_data_skill(explicit_path=skill_path)

    assert result.path == skill_path
    assert result.name == "a-stock-data"
    assert result.version == "3.6.0"


def test_require_rejects_missing_skill_with_install_guidance(tmp_path):
    with pytest.raises(AStockDataSkillError, match="a-stock-data.*安装"):
        require_a_stock_data_skill(explicit_path=tmp_path / "missing" / "SKILL.md")


def test_require_rejects_old_skill_version(tmp_path):
    skill_path = tmp_path / "SKILL.md"
    _write_skill(skill_path, version="3.5.0")

    with pytest.raises(AStockDataSkillError, match="版本过低"):
        require_a_stock_data_skill(explicit_path=skill_path, minimum_version="3.6.0")


def test_discover_rejects_wrong_skill_name(tmp_path):
    skill_path = tmp_path / "SKILL.md"
    _write_skill(skill_path, name="other-skill")

    with pytest.raises(AStockDataSkillError, match="名称不是 a-stock-data"):
        discover_a_stock_data_skill(explicit_path=skill_path)
