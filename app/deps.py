"""依赖注入：认证、DB session、Redis 连接。"""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import APP_ACCESS_TOKEN, APP_USER_ID
from db.database import get_db

_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="AppBearerAuth",
    description="单用户演示环境的 Bearer access token",
)


def _constant_time_equal(value: str, expected: str) -> bool:
    """以 UTF-8 字节比较，避免非 ASCII 输入触发 TypeError。"""
    return secrets.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


def require_access_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """验证 Bearer token；响应和日志均不包含请求凭证。"""
    if credentials is None or not _constant_time_equal(
        credentials.credentials,
        APP_ACCESS_TOKEN,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证失败",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_app_user(user_id: str) -> str:
    """限制请求只能操作服务端配置的单一用户。"""
    if not _constant_time_equal(user_id, APP_USER_ID):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该用户资源",
        )
    return APP_USER_ID
