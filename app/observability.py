"""低敏感可观测性：请求关联 ID 与结构化事件日志。"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SENSITIVE_FIELDS = {
    "authorization",
    "answer",
    "body",
    "content",
    "document",
    "message",
    "password",
    "prompt",
    "question",
    "secret",
    "token",
}


def _new_id() -> str:
    return uuid.uuid4().hex


def _safe_request_id(candidate: str | None) -> str:
    if candidate and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return _new_id()


def get_or_create_request_id() -> str:
    """返回当前请求 ID；非 HTTP 调用（如单元测试）会生成一个。"""
    current = _request_id.get()
    if current is None:
        current = _new_id()
        _request_id.set(current)
    return current


def new_turn_id() -> str:
    return _new_id()


def log_event(
    target_logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    request_id: str | None = None,
    **fields: Any,
) -> None:
    """输出稳定 JSON 事件，并拒绝容易误传正文或凭据的字段名。"""
    unsafe_fields = {name.lower() for name in fields} & _SENSITIVE_FIELDS
    if unsafe_fields:
        names = ", ".join(sorted(unsafe_fields))
        raise ValueError(f"禁止使用敏感日志字段: {names}")

    payload = {
        "event": event,
        "request_id": request_id or get_or_create_request_id(),
        **fields,
    }
    target_logger.log(
        level,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
    )


class RequestObservabilityMiddleware:
    """在完整 ASGI 响应周期内传播 request ID 并记录安全的 RED 基础信号。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_id = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-request-id":
                incoming_id = value.decode("ascii", errors="ignore")
                break

        request_id = _safe_request_id(incoming_id)
        context_token = _request_id.set(request_id)
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            route = getattr(scope.get("route"), "path", scope.get("path", "unknown"))
            failure_stage = None
            if status_code == 401:
                failure_stage = "authentication"
            elif status_code == 403:
                failure_stage = "authorization"
            elif status_code >= 500:
                failure_stage = "request"
            log_event(
                logger,
                "request_completed",
                request_id=request_id,
                method=scope.get("method", "unknown"),
                route=route,
                status_code=status_code,
                status_class=f"{status_code // 100}xx",
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                failure_stage=failure_stage,
            )
            _request_id.reset(context_token)
