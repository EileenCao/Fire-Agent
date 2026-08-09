"""Feishu custom bot Webhook adapter with signing, splitting and retries."""

import base64
import hashlib
import hmac
import json
import time as time_module
from dataclasses import dataclass
from typing import Callable, List, Optional
from urllib import request as urllib_request


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    attempts: int
    response_code: Optional[int] = None
    error: Optional[str] = None


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
            return _UrllibResponse(599, json.dumps({"code": -1, "msg": str(exc)}).encode("utf-8"))


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
        self.max_payload_bytes = max(1, max_payload_bytes)

    def send_markdown(self, content: str) -> DeliveryResult:
        chunks = _split_text(content, max(1, int(self.max_payload_bytes * 0.7)))
        attempts = 0
        last_code = None
        last_error = None
        for chunk in chunks:
            sent = False
            for attempt in range(1, self.max_attempts + 1):
                attempts += 1
                response_code, error = self._send_chunk(chunk)
                last_code, last_error = response_code, error
                if error is None:
                    sent = True
                    break
                if attempt < self.max_attempts:
                    self.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
            if not sent:
                return DeliveryResult(False, attempts, last_code, last_error)
        return DeliveryResult(True, attempts, last_code, None)

    def _send_chunk(self, content: str):
        timestamp = str(int(self.clock()))
        payload = {"msg_type": "text", "content": {"text": content}}
        if self.secret:
            payload["timestamp"] = timestamp
            payload["sign"] = _signature(timestamp, self.secret)
        try:
            response = self.transport.post(
                self.webhook_url, json=payload, timeout=self.timeout
            )
            response_code = getattr(response, "status_code", None)
            body = response.json() if hasattr(response, "json") else {}
            service_code = body.get("code", 0) if isinstance(body, dict) else 0
            if response_code != 200:
                return response_code, "HTTP {}".format(response_code)
            if service_code not in (0, None):
                return response_code, "飞书错误 {}: {}".format(
                    service_code, body.get("msg", "未知错误")
                )
            return response_code, None
        except Exception as exc:
            return response_code if "response_code" in locals() else None, str(exc)


def _signature(timestamp: str, secret: str) -> str:
    string_to_sign = "{}\n{}".format(timestamp, secret)
    digest = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _split_text(content: str, max_bytes: int) -> List[str]:
    if len(content.encode("utf-8")) <= max_bytes:
        return [content]
    chunks = []
    current = []
    current_bytes = 0
    for character in content:
        character_bytes = len(character.encode("utf-8"))
        if current and current_bytes + character_bytes > max_bytes:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += character_bytes
    if current:
        chunks.append("".join(current))
    return chunks
