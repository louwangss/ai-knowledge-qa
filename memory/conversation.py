"""以 MySQL 为权威来源的会话上下文与摘要压缩。"""

import logging

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import ChatHistory, Session as SessionModel, SessionSummary
from memory.short_term import _generate_summary


logger = logging.getLogger(__name__)

INITIAL_COMPRESS_ROUND = 11
COMPRESS_INTERVAL = 5
MESSAGES_PER_COMPRESSION = 10


def _summary_record(
    db: Session,
    user_id: str,
    session_id: str,
) -> SessionSummary | None:
    return db.execute(
        select(SessionSummary)
        .join(SessionModel, SessionModel.id == SessionSummary.session_id)
        .where(SessionSummary.session_id == session_id)
        .where(SessionModel.user_id == user_id)
    ).scalar_one_or_none()


def _message_count(db: Session, user_id: str, session_id: str) -> int:
    return int(db.execute(
        select(func.count(ChatHistory.id))
        .where(ChatHistory.user_id == user_id)
        .where(ChatHistory.session_id == session_id)
    ).scalar_one())


def _ordered_messages(
    db: Session,
    user_id: str,
    session_id: str,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[ChatHistory]:
    statement = (
        select(ChatHistory)
        .where(ChatHistory.user_id == user_id)
        .where(ChatHistory.session_id == session_id)
        .order_by(ChatHistory.created_at.asc(), ChatHistory.id.asc())
        .offset(offset)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.execute(statement).scalars().all())


def _valid_compressed_count(compressed_count: int, total_messages: int) -> bool:
    return (
        compressed_count >= 0
        and compressed_count <= total_messages
        and compressed_count % MESSAGES_PER_COMPRESSION == 0
    )


def load_conversation_memory(db: Session, user_id: str, session_id: str) -> dict:
    """读取持久化摘要及摘要之后的完整消息，Redis 不参与事实恢复。"""
    summary_record = _summary_record(db, user_id, session_id)
    total_messages = _message_count(db, user_id, session_id)
    compressed_count = summary_record.compressed_count if summary_record else 0

    if summary_record and not _valid_compressed_count(compressed_count, total_messages):
        logger.warning(
            "会话摘要游标无效，回退到完整聊天记录: session_id=%s",
            session_id,
        )
        summary = None
        compressed_count = 0
    else:
        summary = summary_record.summary if summary_record else None

    rows = _ordered_messages(
        db,
        user_id,
        session_id,
        offset=compressed_count,
    )
    return {
        "summary": summary,
        "messages": [
            {
                "role": row.role,
                "content": row.content,
                "message_id": str(row.id),
            }
            for row in rows
        ],
    }


def _store_summary_cas(
    db: Session,
    session_id: str,
    summary_record_id: int | None,
    expected_summary: str,
    expected_count: int,
    new_summary: str,
    new_count: int,
) -> bool:
    """只在摘要游标未变化时提交，迟到的 LLM 结果直接丢弃。"""
    if summary_record_id is None:
        db.add(SessionSummary(
            session_id=session_id,
            summary=new_summary,
            compressed_count=new_count,
        ))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False

    result = db.execute(
        update(SessionSummary)
        .where(SessionSummary.id == summary_record_id)
        .where(SessionSummary.compressed_count == expected_count)
        .where(SessionSummary.summary == expected_summary)
        .values(summary=new_summary, compressed_count=new_count)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def maybe_compress_conversation(db: Session, user_id: str, session_id: str) -> bool:
    """按项目既有 11/5 轮策略更新 MySQL 摘要；失败时保留原始消息。"""
    summary_record = _summary_record(db, user_id, session_id)
    total_messages = _message_count(db, user_id, session_id)
    summary_record_id = summary_record.id if summary_record else None
    expected_count = summary_record.compressed_count if summary_record else 0
    expected_summary = summary_record.summary if summary_record else ""
    if not _valid_compressed_count(expected_count, total_messages):
        logger.warning(
            "会话摘要游标无效，跳过自动压缩: session_id=%s",
            session_id,
        )
        db.rollback()
        return False

    completed_rounds = int(db.execute(
        select(func.count(ChatHistory.id))
        .where(ChatHistory.user_id == user_id)
        .where(ChatHistory.session_id == session_id)
        .where(ChatHistory.role == "assistant")
    ).scalar_one())
    completed_compressions = expected_count // MESSAGES_PER_COMPRESSION
    next_round = INITIAL_COMPRESS_ROUND + completed_compressions * COMPRESS_INTERVAL
    if completed_rounds < next_round:
        db.rollback()
        return False

    oldest_uncompressed = _ordered_messages(
        db,
        user_id,
        session_id,
        offset=expected_count,
        limit=MESSAGES_PER_COMPRESSION,
    )
    expected_roles = ["user", "assistant"] * (MESSAGES_PER_COMPRESSION // 2)
    if [row.role for row in oldest_uncompressed] != expected_roles:
        logger.warning(
            "会话消息不是完整问答对，跳过自动压缩: session_id=%s",
            session_id,
        )
        db.rollback()
        return False

    messages = [
        {"role": row.role, "content": row.content}
        for row in oldest_uncompressed
    ]
    # 不在慢速 LLM 调用期间占用数据库事务和连接；后续通过 CAS 防止迟到覆盖。
    db.rollback()
    new_summary = _generate_summary(expected_summary, messages)
    if not new_summary:
        return False

    return _store_summary_cas(
        db,
        session_id,
        summary_record_id,
        expected_summary,
        expected_count,
        new_summary,
        expected_count + len(oldest_uncompressed),
    )
