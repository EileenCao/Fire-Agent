import base64
import hashlib
import hmac
from datetime import date, datetime, timezone

from mcp_server.adapters.feishu import FeishuWebhookClient
from mcp_server.domain.models import DailyReport, MarketSnapshot


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


def test_feishu_report_uses_rich_post_and_falls_back_to_text():
    transport = RecordingTransport(
        [
            FakeResponse(400, {"code": 9499, "msg": "post payload rejected"}),
            FakeResponse(200),
        ]
    )
    client = FeishuWebhookClient(
        webhook_url="https://example.invalid/hook",
        secret="secret",
        transport=transport,
        clock=lambda: 1700000000,
        sleep=lambda _: None,
    )
    report = DailyReport(
        report_date=date(2026, 8, 10),
        cutoff="上午收盘 11:30",
        content="# 午间日报\n\n512890 +2.83%",
        data_as_of=datetime(2026, 8, 10, 11, 30, tzinfo=timezone.utc),
        status="ok",
        snapshots=[
            MarketSnapshot(
                code="512890",
                name="红利ETF",
                instrument_type="ETF",
                price=1.234,
                last_close=1.2,
                change_pct=2.83,
                amount_wan=100.0,
                turnover_pct=0.4,
                pe_ttm=8.1,
                pb=0.86,
                as_of=datetime(2026, 8, 10, 11, 30, tzinfo=timezone.utc),
                source_name="腾讯财经",
                source_url="https://qt.gtimg.cn/",
                skill_name="a-stock-data",
                skill_version="3.6.0",
            )
        ],
    )

    result = client.send_report(report)

    assert result.success is True
    assert [call[1]["msg_type"] for call in transport.calls] == ["post", "text"]
    assert transport.calls[0][1]["content"]["post"]["zh_cn"]["title"]
    assert transport.calls[1][1]["content"]["text"] == report.content
    assert len(result.attempt_records) == 2
