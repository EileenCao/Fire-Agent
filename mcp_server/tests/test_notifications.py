import base64
import hashlib
import hmac

from mcp_server.adapters.feishu import FeishuWebhookClient


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {"code": 0, "msg": "ok"}

    def json(self):
        return self._payload


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return self.responses.pop(0)


def test_feishu_payload_contains_signature_and_markdown_message():
    transport = RecordingTransport([FakeResponse(200)])
    client = FeishuWebhookClient(
        webhook_url="https://example.invalid/hook",
        secret="secret",
        transport=transport,
        clock=lambda: 1700000000,
        sleep=lambda _: None,
    )

    result = client.send_markdown("# 午间日报\n\n512890 +2.83%")

    assert result.success is True
    assert len(transport.calls) == 1
    _, payload, _ = transport.calls[0]
    expected = base64.b64encode(
        hmac.new(
            b"1700000000\nsecret", digestmod=hashlib.sha256
        ).digest()
    ).decode("ascii")
    assert payload["timestamp"] == "1700000000"
    assert payload["sign"] == expected
    assert payload["msg_type"] == "text"
    assert "午间日报" in payload["content"]["text"]


def test_feishu_retries_transient_failures_three_times():
    transport = RecordingTransport(
        [FakeResponse(500), FakeResponse(503), FakeResponse(200)]
    )
    client = FeishuWebhookClient(
        webhook_url="https://example.invalid/hook",
        secret=None,
        transport=transport,
        clock=lambda: 1700000000,
        sleep=lambda _: None,
        max_attempts=3,
    )

    result = client.send_markdown("日报")

    assert result.success is True
    assert result.attempts == 3
    assert len(transport.calls) == 3


def test_feishu_splits_large_markdown_into_safe_messages():
    transport = RecordingTransport([FakeResponse(200) for _ in range(3)])
    client = FeishuWebhookClient(
        webhook_url="https://example.invalid/hook",
        secret=None,
        transport=transport,
        clock=lambda: 1700000000,
        sleep=lambda _: None,
        max_payload_bytes=120,
    )

    result = client.send_markdown("A" * 200)

    assert result.success is True
    assert len(transport.calls) == 3
    assert all(len(str(call[1]["content"]["text"]).encode("utf-8")) <= 120 for call in transport.calls)
