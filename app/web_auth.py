"""仅本机 Web 前端使用的进程内短期会话。"""

import hashlib
import ipaddress
import os
import secrets

from fastapi import HTTPException, Request, status

from config import APP_ACCESS_TOKEN


WEB_SESSION_COOKIE = "ai_knowledge_web_session"
_web_session_hashes: set[str] = set()
_consumed_bootstrap_hashes: set[str] = set()
_DEFAULT_ALLOWED_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    _web_session_hashes.add(_digest(session_value))
    return session_value


def has_valid_web_session(request: Request) -> bool:
    value = request.cookies.get(WEB_SESSION_COOKIE)
    return bool(value) and _digest(value) in _web_session_hashes


def clear_web_sessions_for_test() -> None:
    """隔离测试状态；生产代码不会调用。"""
    _web_session_hashes.clear()
    _consumed_bootstrap_hashes.clear()
