from mcp_server.domain.strategy import StrategySpec


def test_python_plugin_requires_explicit_review_approval():
    payload = {
        "strategy_id": "plugin",
        "version": "1.0.0",
        "name": "插件策略",
        "universe": ["600000"],
        "frequency": "1d",
        "entry": {"rules": []},
        "exit": {"rules": []},
        "position_sizing": {"type": "all_in"},
        "plugin": {"path": "strategy.py"},
    }

    spec = StrategySpec.from_dict(payload)

    assert spec.is_valid is False
    assert any("批准" in error for error in spec.validation_errors)
