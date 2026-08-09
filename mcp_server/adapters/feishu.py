"""Feishu custom bot Webhook adapter with signing, formatting and retries."""

import base64
import hashlib
import hmac
import json
import time as time_module
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib import request as urllib_request

from mcp_server.domain.models import DailyReport, MarketSnapshot


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    attempts: int
    response_code: Optional[int] = None
    error: Optional[str] = None
    content_format: str = "text"
    chunks_sent: int = 0
    chunks_total: int = 0
    attempt_records: List[Dict[str, Any]] = field(default_factory=list)


class UrllibTransport:
    def post(self, url, json, timeout):
        body = __import__("json").dumps(json, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            response = urllib_request.urlopen(request, timeout=timeout)
            return _UrllibResponse(response.getcode(), response.read())
        except Exception as exc:
            return _UrllibResponse(
                599,
                __import__("json").dumps(
                    {"code": -1, "msg": "notification network request failed: {}".format(exc)}
                ).encode("utf-8"),
            )


class _UrllibResponse:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8", errors="replace"))


class FeishuWebhookClient:
    def __init__(
        self,
        webhook_url: str,
        secret: Optional[str] = None,
        transport=None,
        clock: Callable[[], float] = time_module.time,
        sleep: Callable[[float], None] = time_module.sleep,
        timeout: int = 15,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        max_payload_bytes: int = 18 * 1024,
    ):
        if not webhook_url:
            raise ValueError("FEISHU_WEBHOOK_URL 不能为空")
        self.webhook_url = webhook_url
        self.secret = secret
        self.transport = transport or UrllibTransport()
        self.clock = clock
        self.sleep = sleep
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.max_payload_bytes = min(max(1, int(max_payload_bytes)), 20 * 1024 - 1)

    def send_markdown(self, content: str) -> DeliveryResult:
        chunks = _split_text_by_payload(
            content,
            self._text_payload_size,
            self.max_payload_bytes,
        )
        payloads = [self._text_payload(chunk) for chunk in chunks]
        return self._deliver(payloads, content_format="text")

    def send_report(self, report: DailyReport) -> DeliveryResult:
        try:
            payloads = self._post_payloads(report)
        except (TypeError, ValueError):
            return self.send_markdown(report.content)

        rich_result = self._deliver(payloads, content_format="post")
        if rich_result.success or not _is_format_fallback_error(rich_result):
            return rich_result

        text_result = self.send_markdown(report.content)
        return DeliveryResult(
            success=text_result.success,
            attempts=rich_result.attempts + text_result.attempts,
            response_code=text_result.response_code,
            error=text_result.error,
            content_format="text",
            chunks_sent=text_result.chunks_sent,
            chunks_total=text_result.chunks_total,
            attempt_records=rich_result.attempt_records + text_result.attempt_records,
        )

    def _deliver(self, payloads: List[Dict[str, Any]], content_format: str) -> DeliveryResult:
        attempts = 0
        chunks_sent = 0
        records: List[Dict[str, Any]] = []
        last_code = None
        last_error = None
        total = len(payloads)
        for chunk_index, payload in enumerate(payloads):
            sent = False
            for attempt in range(1, self.max_attempts + 1):
                attempts += 1
                response_code, error, retryable = self._send_payload(payload)
                last_code, last_error = response_code, error
                records.append(
                    {
                        "chunk_index": chunk_index,
                        "chunk_count": total,
                        "attempt": attempt,
                        "status": "sent" if error is None else "failed",
                        "response_code": response_code,
                        "error": error,
                        "content_format": content_format,
                    }
                )
                if error is None:
                    sent = True
                    chunks_sent += 1
                    break
                if not retryable or attempt >= self.max_attempts:
                    return DeliveryResult(
                        False,
                        attempts,
                        last_code,
                        last_error,
                        content_format,
                        chunks_sent,
                        total,
                        records,
                    )
                self.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
            if not sent:
                return DeliveryResult(
                    False,
                    attempts,
                    last_code,
                    last_error,
                    content_format,
                    chunks_sent,
                    total,
                    records,
                )
        return DeliveryResult(
            True,
            attempts,
            last_code,
            None,
            content_format,
            chunks_sent,
            total,
            records,
        )

    def _send_payload(self, payload: Dict[str, Any]):
        response_code = None
        try:
            response = self.transport.post(
                self.webhook_url, json=payload, timeout=self.timeout
            )
            response_code = getattr(response, "status_code", None)
            body = response.json() if hasattr(response, "json") else {}
            service_code = body.get("code", 0) if isinstance(body, dict) else 0
            if response_code != 200:
                return response_code, "HTTP {}".format(response_code), _is_transient(response_code)
            if service_code not in (0, None):
                return (
                    response_code,
                    "飞书错误 {}: {}".format(service_code, body.get("msg", "未知错误")),
                    False,
                )
            return response_code, None, False
        except Exception as exc:
            return (
                response_code,
                _safe_error(exc, self.webhook_url, self.secret),
                True,
            )

    def _text_payload(self, content: str) -> Dict[str, Any]:
        return self._signed_payload(
            {"msg_type": "text", "content": {"text": content}}
        )

    def _text_payload_size(self, content: str) -> int:
        payload = {"msg_type": "text", "content": {"text": content}}
        if self.secret:
            timestamp = str(int(self.clock()))
            payload["timestamp"] = timestamp
            payload["sign"] = _signature(timestamp, self.secret)
        return _payload_bytes(payload)

    def _post_payloads(self, report: DailyReport) -> List[Dict[str, Any]]:
        rows = _report_rows(report)
        if not rows:
            raise ValueError("日报没有可发送内容")
        title = "A股午间观察日报 {}".format(report.report_date.isoformat())
        chunks: List[List[List[Dict[str, str]]]] = []
        current: List[List[Dict[str, str]]] = []
        for row in rows:
            candidate = current + [row]
            try:
                payload = self._post_payload(title, candidate, 1, 1)
            except ValueError:
                payload = None
            if current and (
                payload is None or _payload_bytes(payload) > self.max_payload_bytes
            ):
                chunks.append(current)
                current = [row]
                if _payload_bytes(self._post_payload(title, current, 1, 1)) > self.max_payload_bytes:
                    raise ValueError("单个日报段超过飞书请求体限制")
            else:
                current = candidate
        if current:
            chunks.append(current)
        total = len(chunks)
        return [
            self._post_payload(title, chunk, index + 1, total)
            for index, chunk in enumerate(chunks)
        ]

    def _post_payload(self, title, rows, index, total):
        suffix = "" if total == 1 else " [{}/{}]".format(index, total)
        return self._signed_payload(
            {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": title + suffix,
                            "content": rows,
                        }
                    }
                },
            }
        )

    def _signed_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.secret:
            timestamp = str(int(self.clock()))
            payload = dict(payload)
            payload["timestamp"] = timestamp
            payload["sign"] = _signature(timestamp, self.secret)
        if _payload_bytes(payload) > self.max_payload_bytes:
            raise ValueError("飞书请求体超过安全大小限制")
        return payload


def _report_rows(report: DailyReport) -> List[List[Dict[str, str]]]:
    rows: List[List[Dict[str, str]]] = [
        [{"tag": "text", "text": "截至{}；本报告只使用上午盘数据。".format(report.cutoff)}]
    ]
    for item in report.snapshots:
        rows.extend(_snapshot_rows(item))
    if not report.snapshots:
        rows.append([{"tag": "text", "text": "观察清单为空，未生成标的数据。"}])
    return rows


def _snapshot_rows(item: MarketSnapshot) -> List[List[Dict[str, str]]]:
    rows = [
        [{"tag": "text", "text": "{}（{}，{}）".format(item.name or "未命名标的", item.code, item.instrument_type)}],
        [{"tag": "text", "text": "行情：{}；上午涨跌：{}；PE(TTM)：{}；PB：{}".format(
            _number(item.price), _percent(item.change_pct), _multiple(item.pe_ttm), _multiple(item.pb)
        )}],
    ]
    source = [{"tag": "text", "text": "数据时间：{}；来源：".format(item.as_of.isoformat() if item.as_of else "缺失")}]
    if item.source_url:
        source.append({"tag": "a", "text": item.source_name or "未知来源", "href": item.source_url})
    else:
        source.append({"tag": "text", "text": item.source_name or "未知来源"})
    rows.append(source)
    if item.skill_name or item.skill_version:
        rows.append([{"tag": "text", "text": "数据 Skill：{}；版本：{}".format(item.skill_name or "缺失", item.skill_version or "缺失")}])
    if item.signals:
        rows.append([{"tag": "text", "text": "规则信号：{}".format("；".join(item.signals))}])
    for warning in item.warnings:
        rows.append([{"tag": "text", "text": "⚠️ {}".format(warning)}])
    for error in item.errors:
        rows.append([{"tag": "text", "text": "❌ {}".format(error)}])
    return rows


def _is_format_fallback_error(result: DeliveryResult) -> bool:
    return (
        not result.success
        and result.chunks_sent == 0
        and result.response_code is not None
        and result.response_code < 500
    )


def _is_transient(response_code: Optional[int]) -> bool:
    return response_code is None or response_code == 429 or response_code >= 500


def _payload_bytes(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _signature(timestamp: str, secret: str) -> str:
    string_to_sign = "{}\n{}".format(timestamp, secret)
    digest = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _safe_error(exc, webhook_url: str, secret: Optional[str]) -> str:
    message = str(exc) or exc.__class__.__name__
    message = message.replace(webhook_url, "<redacted>")
    if secret:
        message = message.replace(secret, "<redacted>")
    return message


def _split_text_by_payload(
    content: str,
    payload_size: Callable[[str], int],
    max_bytes: int,
) -> List[str]:
    chunks = []
    current = []
    for character in content:
        candidate = "".join(current) + character
        if current and payload_size(candidate) > max_bytes:
            chunks.append("".join(current))
            current = [character]
        else:
            current.append(character)
    if current:
        chunks.append("".join(current))
    return chunks or [""]


def _number(value: Optional[float]) -> str:
    return "数据缺失" if value is None else "{:.3f}".format(value)


def _percent(value: Optional[float]) -> str:
    return "数据缺失" if value is None else "{:.2f}%".format(value)


def _multiple(value: Optional[float]) -> str:
    return "数据缺失" if value is None or value <= 0 else "{:.2f}x".format(value)
