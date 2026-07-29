"""仅本机 Web 前端使用的进程内短期会话。"""

import hashlib
import ipaddress
import os
import secrets

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from config import (
    APP_ACCESS_TOKEN,
    WEB_LOGIN_MAX_ATTEMPTS,
    WEB_LOGIN_WINDOW_SECONDS,
    WEB_SESSION_TTL_SECONDS,
)
from memory.redis_client import get_redis


WEB_SESSION_COOKIE = "ai_knowledge_web_session"
_consumed_bootstrap_hashes: set[str] = set()
_WEB_SESSION_KEY_PREFIX = "qa:web_session:"
_WEB_LOGIN_ATTEMPT_KEY_PREFIX = "qa:web_login_attempts:"
_DEFAULT_ALLOWED_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _session_key(value: str) -> str:
    return f"{_WEB_SESSION_KEY_PREFIX}{_digest(value)}"


def _login_attempt_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{_WEB_LOGIN_ATTEMPT_KEY_PREFIX}{_digest(host)}"


def _session_store_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="会话服务暂时不可用",
    )


def require_web_login_capacity(request: Request) -> None:
    try:
        attempts = get_redis().get(_login_attempt_key(request))
    except RedisError as exc:
        raise _session_store_unavailable() from exc
    if attempts is not None and int(attempts) >= WEB_LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="认证尝试过于频繁，请稍后重试",
            headers={"Retry-After": str(WEB_LOGIN_WINDOW_SECONDS)},
        )


def record_web_login_failure(request: Request) -> None:
    try:
        redis_client = get_redis()
        key = _login_attempt_key(request)
        attempts = redis_client.incr(key)
        if attempts == 1:
            redis_client.expire(key, WEB_LOGIN_WINDOW_SECONDS)
    except RedisError as exc:
        raise _session_store_unavailable() from exc


def clear_web_login_failures(request: Request) -> None:
    try:
        get_redis().delete(_login_attempt_key(request))
    except RedisError as exc:
        raise _session_store_unavailable() from exc


def is_loopback_client(request: Request) -> bool:
    """只信任实际 TCP 客户端地址，不读取可伪造的转发头。"""
    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def allowed_web_origins() -> set[str]:
    """返回同源静态页和 Vite 开发页的精确 Origin allowlist。"""
    configured = os.getenv("APP_WEB_ORIGINS", "")
    origins = {item.strip().rstrip("/") for item in configured.split(",") if item.strip()}
    return origins or set(_DEFAULT_ALLOWED_ORIGINS)


def require_allowed_web_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in allowed_web_origins():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="来源不受信任")


def exchange_web_credential(value: str) -> str:
    """验证长期 token 或单次启动凭证，返回全新的 HttpOnly 会话值。"""
    bootstrap = os.getenv("APP_WEB_BOOTSTRAP_TOKEN", "")
    bootstrap_hash = _digest(bootstrap) if bootstrap else ""
    is_access_token = secrets.compare_digest(value.encode(), APP_ACCESS_TOKEN.encode())
    is_unused_bootstrap = bool(bootstrap) and secrets.compare_digest(
        value.encode(), bootstrap.encode()
    ) and bootstrap_hash not in _consumed_bootstrap_hashes
    if not is_access_token and not is_unused_bootstrap:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证失败",
        )

    if is_unused_bootstrap:
        _consumed_bootstrap_hashes.add(bootstrap_hash)
    session_value = secrets.token_urlsafe(32)
    try:
        get_redis().set(
            _session_key(session_value),
            "active",
            ex=WEB_SESSION_TTL_SECONDS,
        )
    except RedisError as exc:
        raise _session_store_unavailable() from exc
    return session_value


def has_valid_web_session(request: Request) -> bool:
    value = request.cookies.get(WEB_SESSION_COOKIE)
    if not value:
        return False
    try:
        return bool(get_redis().exists(_session_key(value)))
    except RedisError as exc:
        raise _session_store_unavailable() from exc


def revoke_web_session(request: Request) -> None:
    value = request.cookies.get(WEB_SESSION_COOKIE)
    if not value:
        return
    try:
        get_redis().delete(_session_key(value))
    except RedisError as exc:
        raise _session_store_unavailable() from exc


def clear_web_auth_process_state_for_test() -> None:
    """隔离测试状态；生产代码不会调用。"""
    _consumed_bootstrap_hashes.clear()
