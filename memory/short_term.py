"""短期记忆（Redis）：summary + messages + round_count + next_compress_round"""
import json
import logging

import redis

from config import REDIS_URL
from rag.llm import get_llm

logger = logging.getLogger(__name__)

_TTL = 1800  # 30 分钟
_INITIAL_COMPRESS_ROUND = 11  # 首次压缩触发点
_COMPRESS_INTERVAL = 5  # 后续每 5 轮一次
_MESSAGES_PER_ROUND = 2  # 一轮 = user + assistant

_pool: redis.ConnectionPool | None = None


def get_redis() -> redis.Redis:
    """单例 Redis 连接"""
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
    return redis.Redis(connection_pool=_pool)


# ---- Key 命名 ----

def _key(user_id: str, session_id: str, suffix: str) -> str:
    return f"qa:{user_id}:session:{session_id}:{suffix}"


# ---- 写入 ----

def append_message(r: redis.Redis, user_id: str, session_id: str, role: str, content: str):
    """追加一条消息到 Redis messages 列表"""
    r.rpush(
        _key(user_id, session_id, "messages"),
        json.dumps({"role": role, "content": content}),
    )


def increment_round(r: redis.Redis, user_id: str, session_id: str) -> int:
    """round_count + 1，返回新值"""
    new_val = r.incr(_key(user_id, session_id, "round_count"))
    renew_ttl(r, user_id, session_id)
    return new_val


def decrement_round(r: redis.Redis, user_id: str, session_id: str):
    """round_count - 1（cleanup 用）"""
    r.decr(_key(user_id, session_id, "round_count"))


def renew_ttl(r: redis.Redis, user_id: str, session_id: str):
    """统一续期 4 个 key"""
    for suffix in ["summary", "messages", "round_count", "next_compress_round"]:
        r.expire(_key(user_id, session_id, suffix), _TTL)


# ---- 读取 ----

def get_summary(r: redis.Redis, user_id: str, session_id: str) -> str | None:
    return r.get(_key(user_id, session_id, "summary"))


def get_messages(r: redis.Redis, user_id: str, session_id: str, count: int = -1) -> list[dict]:
    """读取消息列表。count=-1 读全部，否则读最后 count 条"""
    messages_key = _key(user_id, session_id, "messages")
    if count == -1:
        raw = r.lrange(messages_key, 0, -1)
    else:
        raw = r.lrange(messages_key, -count, -1)
    return [json.loads(item) for item in raw]


def get_round_count(r: redis.Redis, user_id: str, session_id: str) -> int:
    val = r.get(_key(user_id, session_id, "round_count"))
    return int(val) if val else 0


def get_next_compress_round(r: redis.Redis, user_id: str, session_id: str) -> int:
    val = r.get(_key(user_id, session_id, "next_compress_round"))
    return int(val) if val else _INITIAL_COMPRESS_ROUND


# ---- Session 初始化 ----

def init_session(r: redis.Redis, user_id: str, session_id: str, db_messages: list[dict] | None = None):
    """新 session 初始化。

    Args:
        db_messages: 从 MySQL 加载的最近 5 轮对话（user+assistant 成对，正序）
    """
    if db_messages:
        messages_key = _key(user_id, session_id, "messages")
        pipe = r.pipeline()
        for msg in db_messages:
            pipe.rpush(messages_key, json.dumps(msg))
        pipe.execute()
        loaded_rounds = len(db_messages) // _MESSAGES_PER_ROUND
    else:
        loaded_rounds = 0

    r.set(_key(user_id, session_id, "round_count"), loaded_rounds)
    r.set(_key(user_id, session_id, "next_compress_round"), _INITIAL_COMPRESS_ROUND)
    renew_ttl(r, user_id, session_id)


# ---- 读取短期记忆（Redis miss 则从 MySQL 恢复）----

def get_short_term_memory(r: redis.Redis, user_id: str, session_id: str, db_session=None) -> dict:
    """获取短期记忆上下文

    Returns:
        {"summary": str|None, "messages": list[dict]}
    """
    summary = get_summary(r, user_id, session_id)
    messages = get_messages(r, user_id, session_id, count=10)

    # Redis miss（过期/新会话）→ 从 MySQL 恢复
    if not messages and db_session:
        messages = _load_from_mysql(db_session, user_id, session_id)
        if messages:
            init_session(r, user_id, session_id, db_messages=messages)
            summary = None  # 新 session 不继承旧摘要

    return {"summary": summary, "messages": messages}


def _load_from_mysql(db_session, user_id: str, session_id: str) -> list[dict]:
    """从 MySQL 加载当前会话最近 5 轮对话"""
    from db.models import ChatHistory
    from sqlalchemy import select

    stmt = (
        select(ChatHistory)
        .where(ChatHistory.user_id == user_id)
        .where(ChatHistory.session_id == session_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(10)
    )
    rows = db_session.execute(stmt).scalars().all()
    rows = list(reversed(rows))  # 正序
    return [{"role": row.role, "content": row.content} for row in rows]


# ---- 批量摘要压缩 ----

def maybe_compress(r: redis.Redis, user_id: str, session_id: str):
    """检查是否需要触发压缩，需要则执行"""
    round_count = get_round_count(r, user_id, session_id)
    next_round = get_next_compress_round(r, user_id, session_id)

    if round_count < next_round:
        return

    _do_compress(r, user_id, session_id)
    r.set(_key(user_id, session_id, "next_compress_round"), next_round + _COMPRESS_INTERVAL)


def _do_compress(r: redis.Redis, user_id: str, session_id: str):
    """安全压缩：每次移除最旧 5 轮 = 10 条消息"""
    summary_key = _key(user_id, session_id, "summary")
    messages_key = _key(user_id, session_id, "messages")

    # 1. 读取现有摘要
    existing_summary = r.get(summary_key) or ""

    # 2. 读取最旧 10 条消息
    raw_old = r.lrange(messages_key, 0, 9)
    if not raw_old:
        return
    old_messages = [json.loads(item) for item in raw_old]

    # 3. LLM 合并摘要
    new_summary = _generate_summary(existing_summary, old_messages)
    if not new_summary:
        logger.warning("摘要生成失败，跳过压缩")
        return

    # 4. 先写新摘要
    r.set(summary_key, new_summary)

    # 5. 再截断消息（删前 10 条，保留剩余）
    r.ltrim(messages_key, 10, -1)

    logger.info(f"压缩完成: session={session_id}, 移除 {len(raw_old)} 条消息")


def _generate_summary(existing_summary: str, old_messages: list[dict]) -> str:
    """调用 LLM 生成合并摘要"""
    llm = get_llm(temperature=0.0)

    conversation = "\n".join(
        f"{msg['role']}: {msg['content'][:200]}" for msg in old_messages
    )

    prompt = f"""请将以下对话内容合并为一个简洁的摘要（约 200~300 字）。
保留关键信息（讨论的主题、重要结论），省略无关细节。

"""
    if existing_summary:
        prompt += f"已有摘要：\n{existing_summary}\n\n"
    prompt += f"新对话内容：\n{conversation}\n\n请输出合并后的摘要："

    try:
        resp = llm.invoke(prompt)
        return f"[以下为早期对话的摘要，可能遗漏部分细节]\n{resp.content}"
    except Exception as e:
        logger.error(f"摘要 LLM 调用失败: {e}")
        return ""


# ---- Cleanup（失败回滚）----

def cleanup_failed_turn(r: redis.Redis, user_id: str, session_id: str):
    """失败后清理 Redis：RPOP 最后一条消息，DECR round_count"""
    try:
        r.rpop(_key(user_id, session_id, "messages"))
    except Exception:
        pass
    try:
        decrement_round(r, user_id, session_id)
    except Exception:
        pass
