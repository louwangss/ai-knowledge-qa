"""情景记忆：关键事件记录 + 去重"""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import select, text

from db.models import EpisodicMemory

logger = logging.getLogger(__name__)

_DEDUP_WINDOW_SECONDS = 30


def record_event(
    db: Session,
    user_id: str,
    session_id: str,
    event_type: str,
    content: str,
    question_text: str | None = None,
):
    """记录情景事件，自动去重。

    去重策略（按事件类型分两种）：
    - qa_completed（有 question_text）：同事件 + 同 question + 30 秒内
    - document_loaded / note_saved（无 question_text）：同事件 + 同 content + 30 秒内
    """
    threshold = datetime.utcnow() - timedelta(seconds=_DEDUP_WINDOW_SECONDS)

    if question_text:
        existing = db.execute(
            select(EpisodicMemory).where(
                EpisodicMemory.user_id == user_id,
                EpisodicMemory.event_type == event_type,
                EpisodicMemory.question_text == question_text,
                EpisodicMemory.created_at > threshold,
            ).limit(1)
        ).scalar_one_or_none()
    else:
        existing = db.execute(
            select(EpisodicMemory).where(
                EpisodicMemory.user_id == user_id,
                EpisodicMemory.event_type == event_type,
                EpisodicMemory.content == content,
                EpisodicMemory.created_at > threshold,
            ).limit(1)
        ).scalar_one_or_none()

    if existing:
        logger.debug(f"跳过重复事件: {event_type}")
        return

    record = EpisodicMemory(
        user_id=user_id,
        session_id=session_id,
        event_type=event_type,
        content=content,
        question_text=question_text,
    )
    db.add(record)
    db.commit()


def get_recent_events(db: Session, user_id: str, limit: int = 5) -> list[EpisodicMemory]:
    """获取最近 N 条情景记忆"""
    return db.execute(
        select(EpisodicMemory)
        .where(EpisodicMemory.user_id == user_id)
        .order_by(EpisodicMemory.created_at.desc())
        .limit(limit)
    ).scalars().all()
