import textwrap

import pytest

from mcp_server.services.plugin_runner import PluginExecutionError, PythonStrategyPluginRunner


def _write(path, source):
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def test_plugin_runner_returns_standard_actions(tmp_path):
    plugin = tmp_path / "strategy.py"
    _write(
        plugin,
        """
        def run(context):
            return [{"code": context["code"], "action": "BUY", "reason": "test"}]
        """,
    )

    result = PythonStrategyPluginRunner(timeout_seconds=2).run(
        plugin, {"code": "512890", "price": 1.2}
    )

    assert result == [{"code": "512890", "action": "BUY", "reason": "test"}]


def test_plugin_runner_rejects_network_dependency(tmp_path):
    plugin = tmp_path / "strategy.py"
    _write(
        plugin,
        """
        import requests
        def run(context):
            return []
        """,
    )

    with pytest.raises(PluginExecutionError, match="依赖不在白名单"):
        PythonStrategyPluginRunner().run(plugin, {})


def test_plugin_runner_enforces_timeout(tmp_path):
    plugin = tmp_path / "strategy.py"
    _write(
        plugin,
        """
        def run(context):
            while True:
                pass
        """,
    )

    with pytest.raises(PluginExecutionError, match="超时"):
        PythonStrategyPluginRunner(timeout_seconds=0.1).run(plugin, {})
