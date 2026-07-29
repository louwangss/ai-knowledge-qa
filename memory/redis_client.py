"""Redis 客户端：Web 短期状态与旧对话 key 的定向清理。"""

import redis

from config import REDIS_URL


_pool: redis.ConnectionPool | None = None
_LEGACY_CONVERSATION_SUFFIXES = (
    "summary",
    "messages",
    "round_count",
    "next_compress_round",
)


def get_redis() -> redis.Redis:
    """复用进程内连接池创建 Redis 客户端。"""
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
    return redis.Redis(connection_pool=_pool)


def delete_legacy_conversation_keys(user_id: str, session_id: str) -> int:
    """定向删除升级前遗留的派生对话 key，不读取或重建其中内容。"""
    keys = tuple(
        f"qa:{user_id}:session:{session_id}:{suffix}"
        for suffix in _LEGACY_CONVERSATION_SUFFIXES
    )
    return int(get_redis().delete(*keys))
