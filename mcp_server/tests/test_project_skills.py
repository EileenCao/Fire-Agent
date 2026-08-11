from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[2]
SKILLS = {
    "strategy-workbench": "将自然语言策略",
    "backtest-analysis": "分析已保存的回测",
    "daily-strategy-observer": "生成日维度",
    "stock-research": "单标的研究卡",
    "user-memory": "用户长期记忆",
    "sentiment-research": "新闻与博主情绪研究",
}


def test_project_skills_have_discoverable_contracts():
    for name, overview in SKILLS.items():
        path = PROJECT_ROOT / "skills" / name / "SKILL.md"
        assert path.exists(), "缺少项目 Skill：{}".format(name)
        text = path.read_text(encoding="utf-8")
        assert "name: {}".format(name) in text
        assert "description: Use when" in text
        assert overview in text
        assert "a-stock-data" in text
        assert "用户确认" in text
        assert "不自动下单" in text
        if name != "user-memory":
            assert "get_memory_context" in text
            assert "memory_refs" in text
