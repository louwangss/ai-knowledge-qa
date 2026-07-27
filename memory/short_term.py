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

def _serialize_message(
    role: str,
    content: str,
    message_id: str | int | None = None,
) -> str:
    message = {"role": role, "content": content}
    if message_id is not None:
        message["message_id"] = str(message_id)
    return json.dumps(message)


def append_message(
    r: redis.Redis,
    user_id: str,
    session_id: str,
    role: str,
    content: str,
    message_id: str | int | None = None,
):
    """追加一条消息到 Redis messages 列表。

    message_id 对应 MySQL chat_history.id，用于失败时精确移除当前消息。
    兼容历史 Redis 数据：恢复或旧调用没有 ID 时仍可正常读取。
    """
    r.rpush(
        _key(user_id, session_id, "messages"),
        _serialize_message(role, content, message_id),
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


def delete_session_memory(r: redis.Redis, user_id: str, session_id: str) -> int:
    """删除一个会话的全部短期记忆 key，返回实际删除数量。"""
    keys = [
        _key(user_id, session_id, suffix)
        for suffix in ["summary", "messages", "round_count", "next_compress_round"]
    ]
    return r.delete(*keys)


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

def restore_from_mysql_if_needed(r: redis.Redis, user_id: str, session_id: str, db_session):
    """检查 Redis 是否过期或数据不完整，若需要则从 MySQL 恢复历史消息和摘要。

    必须在 append_message（写入当前消息）之前调用，
    否则 Redis 中已有当前消息会导致 not messages 判断失效。
    """
    existing = get_messages(r, user_id, session_id, count=-1)
    mysql_messages = _load_from_mysql(db_session, user_id, session_id)

    if existing and mysql_messages and len(existing) >= len(mysql_messages):
        logger.info(f"[restore] Redis 有 {len(existing)} 条消息，数据完整，跳过恢复: session={session_id}")
        return

    if not mysql_messages:
        logger.info(f"[restore] MySQL 无历史消息: session={session_id}")
        return

    # Redis 为空 或 Redis 消息数少于 MySQL（说明 Redis 曾过期后被部分重建）
    logger.info(
        f"[restore] Redis({len(existing)}条) 不完整或为空，从 MySQL 恢复 {len(mysql_messages)} 条: session={session_id}"
    )
    # 清除 Redis 残留数据后完整恢复
    r.delete(_key(user_id, session_id, "messages"))
    init_session(r, user_id, session_id, db_messages=mysql_messages)

    # 恢复 MySQL 中持久化的 summary
    mysql_summary = _load_summary_from_mysql(db_session, session_id)
    if mysql_summary:
        r.set(_key(user_id, session_id, "summary"), mysql_summary)
        logger.info(f"[restore] 恢复 summary ({len(mysql_summary)} 字符): session={session_id}")

    # 根据实际轮数修正 next_compress_round，避免恢复后立即误触发压缩
    loaded_rounds = len(mysql_messages) // _MESSAGES_PER_ROUND
    if loaded_rounds >= _INITIAL_COMPRESS_ROUND:
        # 已经超过首次压缩点，next 应在当前轮数之后的下一个间隔
        next_round = ((loaded_rounds // _COMPRESS_INTERVAL) + 1) * _COMPRESS_INTERVAL + 1
        r.set(_key(user_id, session_id, "next_compress_round"), next_round)
        logger.info(f"[restore] 修正 next_compress_round={next_round}（已恢复 {loaded_rounds} 轮）")


def get_short_term_memory(r: redis.Redis, user_id: str, session_id: str, db_session=None) -> dict:
    """获取短期记忆上下文

    Returns:
        {"summary": str|None, "messages": list[dict]}
    """
    summary = get_summary(r, user_id, session_id)
    messages = get_messages(r, user_id, session_id, count=-1)
    logger.info(f"[get_stm] Redis messages={len(messages)}, summary={'有' if summary else '无'}: session={session_id}")

    # Redis miss（过期/新会话）→ 从 MySQL 恢复
    if not messages and db_session:
        messages = _load_from_mysql(db_session, user_id, session_id)
        logger.info(f"[get_stm] MySQL 恢复得到 {len(messages)} 条消息: session={session_id}")
        if messages:
            init_session(r, user_id, session_id, db_messages=messages)
        # 从 MySQL 恢复 summary（无论 messages 是否存在）
        summary = _load_summary_from_mysql(db_session, session_id)

    return {"summary": summary, "messages": messages}


def _load_summary_from_mysql(db_session, session_id: str) -> str | None:
    """从 MySQL session_summary 表读取持久化的摘要"""
    from db.models import SessionSummary

    record = db_session.query(SessionSummary).filter(
        SessionSummary.session_id == session_id,
    ).first()
    return record.summary if record else None


def _load_from_mysql(db_session, user_id: str, session_id: str) -> list[dict]:
    """从 MySQL 加载当前会话尚未被摘要压缩的对话（Redis miss 恢复用）

    如果 session_summary 中记录了 compressed_count，则跳过前 N 条已压缩的消息。
    """
    from db.models import ChatHistory, SessionSummary
    from sqlalchemy import select

    # 查已压缩的消息条数
    summary_rec = db_session.query(SessionSummary).filter(
        SessionSummary.session_id == session_id,
    ).first()
    skip_count = summary_rec.compressed_count if summary_rec and summary_rec.compressed_count else 0

    stmt = (
        select(ChatHistory)
        .where(ChatHistory.user_id == user_id)
        .where(ChatHistory.session_id == session_id)
        .order_by(ChatHistory.created_at.asc())
    )
    rows = db_session.execute(stmt).scalars().all()
    return [
        {
            "role": row.role,
            "content": row.content,
            "message_id": str(row.id),
        }
        for row in rows[skip_count:]
    ]


# ---- 批量摘要压缩 ----

def maybe_compress(r: redis.Redis, user_id: str, session_id: str, db_session=None):
    """检查是否需要触发压缩，需要则执行

    Args:
        db_session: 可选的 SQLAlchemy Session，透传给 _do_compress
    """
    round_count = get_round_count(r, user_id, session_id)
    next_round = get_next_compress_round(r, user_id, session_id)

    if round_count < next_round:
        return

    _do_compress(r, user_id, session_id, db_session=db_session)
    r.set(_key(user_id, session_id, "next_compress_round"), next_round + _COMPRESS_INTERVAL)


def _do_compress(r: redis.Redis, user_id: str, session_id: str, db_session=None):
    """安全压缩：每次移除最旧 5 轮 = 10 条消息

    Args:
        db_session: 可选的 SQLAlchemy Session，传入时将摘要持久化到 MySQL
    """
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

    # 4. 先写新摘要到 Redis
    r.set(summary_key, new_summary)

    # 5. 截断消息（删前 10 条，保留剩余）
    r.ltrim(messages_key, 10, -1)

    # 6. 持久化到 MySQL（upsert 逻辑）
    if db_session is not None:
        # 累计已压缩的消息条数（当前已有 compressed_count + 本次压缩的条数）
        prev_compressed = 0
        if db_session is not None:
            from db.models import SessionSummary as _SS
            existing_rec = db_session.query(_SS).filter(
                _SS.session_id == session_id,
            ).first()
            if existing_rec:
                prev_compressed = existing_rec.compressed_count or 0
        total_compressed = prev_compressed + len(raw_old)
        _persist_summary_to_mysql(db_session, session_id, new_summary, total_compressed)

    logger.info(f"压缩完成: session={session_id}, 移除 {len(raw_old)} 条消息")


def _persist_summary_to_mysql(db_session, session_id: str, summary: str, compressed_count: int = 0):
    """将摘要 upsert 到 session_summary 表"""
    from db.models import SessionSummary

    existing = db_session.query(SessionSummary).filter(
        SessionSummary.session_id == session_id,
    ).first()

    if existing:
        existing.summary = summary
        existing.compressed_count = compressed_count
    else:
        db_session.add(SessionSummary(
            session_id=session_id,
            summary=summary,
            compressed_count=compressed_count,
        ))
    db_session.commit()


def _generate_summary(existing_summary: str, old_messages: list[dict]) -> str:
    """调用 LLM 生成合并摘要"""
    llm = get_llm(temperature=0.0)

    conversation = "\n".join(
        f"{msg['role']}: {msg['content'][:200]}" for msg in old_messages
    )

    prompt = f"""请将以下对话内容合并为一个简洁的摘要（约 200~300 字）。
请在摘要开头标注这段对话的大致时间范围。
保留关键信息（讨论的主题、重要结论、涉及的文档或笔记），省略无关细节。
思考步骤：先识别对话讨论了哪些主题，再提取每个主题的关键结论，最后组织成连贯的摘要。

"""
    if existing_summary:
        prompt += f"已有摘要：\n{existing_summary}\n\n"
    prompt += f"新对话内容：\n{conversation}\n\n请输出合并后的摘要："

    try:
        resp = llm.invoke(prompt)
        return f"[以下为早期对话的摘要，可能遗漏部分细节]\n{resp.content}"
    except Exception as e:
        logger.error("摘要 LLM 调用失败: error_type=%s", type(e).__name__)
        return ""


# ---- Cleanup（失败回滚）----

def cleanup_failed_turn(
    r: redis.Redis,
    user_id: str,
    session_id: str,
    message_id: str | int,
    content: str,
    decrement_round_count: bool,
) -> bool:
    """精确清理失败请求写入 Redis 的 user 消息。

    只删除 message_id 匹配的消息，避免并发请求追加后误删列表尾部。
    仅当消息确实被删除且本请求曾成功增加轮数时才回退 round_count。
    """
    messages_key = _key(user_id, session_id, "messages")
    encoded_message = _serialize_message("user", content, message_id)

    try:
        removed = bool(r.lrem(messages_key, 1, encoded_message))
    except Exception as exc:
        logger.warning(
            "清理失败消息的 Redis 记录失败: error_type=%s",
            type(exc).__name__,
        )
        return False

    if removed and decrement_round_count:
        try:
            if get_round_count(r, user_id, session_id) > 0:
                decrement_round(r, user_id, session_id)
        except Exception as exc:
            logger.warning(
                "回退失败消息的 round_count 失败: error_type=%s",
                type(exc).__name__,
            )

    return removed
