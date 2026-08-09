from mcp_server.runtime import build_notifier, build_store


def test_feishu_is_not_enabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FIREAGENT_ENABLE_FEISHU", raising=False)
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/hook")

    store = build_store(tmp_path)

    assert store.notification_status()["channel"] is None
    assert build_notifier() is None
