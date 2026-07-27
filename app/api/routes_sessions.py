"""会话路由：POST 创建、GET 列表、DELETE 删除"""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_app_user
from app.models.schemas import SessionCreate, SessionResponse
from db.models import (
    Session as SessionModel,
    ChatHistory,
    EpisodicMemory,
    SessionSummary,
    User,
)
from memory.short_term import delete_session_memory, get_redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse)
def create_session(payload: SessionCreate, db: Session = Depends(get_db)):
    require_app_user(payload.user_id)
    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    session = SessionModel(
        id=str(uuid.uuid4()),
        user_id=payload.user_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("", response_model=list[SessionResponse])
def list_sessions(user_id: str = Query(...), db: Session = Depends(get_db)):
    """获取用户所有会话，按 last_active 降序"""
    require_app_user(user_id)
    return db.query(SessionModel).filter(
        SessionModel.user_id == user_id,
        SessionModel.status == "active",
    ).order_by(SessionModel.last_active.desc()).all()


@router.delete("/{session_id}")
def delete_session(session_id: str, user_id: str = Query(...), db: Session = Depends(get_db)):
    """硬删除会话 + 关联的 chat_history + episodic_memory"""
    require_app_user(user_id)
    session = db.query(SessionModel).filter(
        SessionModel.id == session_id,
        SessionModel.user_id == user_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        db.query(ChatHistory).filter(ChatHistory.session_id == session_id).delete()
        db.query(EpisodicMemory).filter(EpisodicMemory.session_id == session_id).delete()
        db.query(SessionSummary).filter(SessionSummary.session_id == session_id).delete()
        db.delete(session)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Redis 是可重建派生状态；数据库删除成功后尽力清理，失败时由 TTL 最终回收。
    try:
        delete_session_memory(get_redis(), user_id, session_id)
    except Exception as exc:
        logger.warning("会话已删除，但 Redis 短期记忆清理失败: %s", exc)

    return {"detail": "删除成功"}
