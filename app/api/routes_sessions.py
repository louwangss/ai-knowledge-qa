"""会话路由：POST 创建、GET 列表、DELETE 删除"""
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_app_user
from app.models.schemas import SessionCreate, SessionResponse
from db.models import (
    Session as SessionModel,
    ChatHistory,
    ChatSource,
    ChatTurn,
    EpisodicMemory,
    SessionSummary,
    User,
)
from memory.redis_client import delete_legacy_conversation_keys


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
def list_sessions(
    user_id: str = Query(...),
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    """获取用户所有会话，按 last_active 降序"""
    require_app_user(user_id)
    return db.query(SessionModel).filter(
        SessionModel.user_id == user_id,
        SessionModel.status == "active",
    ).order_by(
        SessionModel.last_active.desc(),
        SessionModel.created_at.desc(),
        SessionModel.id.desc(),
    ).offset(offset).limit(limit).all()


@router.delete("/{session_id}")
def delete_session(session_id: str, user_id: str = Query(...), db: Session = Depends(get_db)):
    """硬删除会话及关联的消息、来源、摘要与情景记忆。"""
    require_app_user(user_id)
    session = db.query(SessionModel).filter(
        SessionModel.id == session_id,
        SessionModel.user_id == user_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        db.query(ChatTurn).filter(ChatTurn.session_id == session_id).delete()
        message_ids = db.query(ChatHistory.id).filter(
            ChatHistory.session_id == session_id,
        )
        db.query(ChatSource).filter(
            ChatSource.message_id.in_(message_ids),
        ).delete(synchronize_session=False)
        db.query(ChatHistory).filter(ChatHistory.session_id == session_id).delete()
        db.query(EpisodicMemory).filter(EpisodicMemory.session_id == session_id).delete()
        db.query(SessionSummary).filter(SessionSummary.session_id == session_id).delete()
        db.delete(session)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # 兼容从旧 Redis 对话缓存版本升级后的硬删除语义；当前版本不再写入这些 key。
    try:
        delete_legacy_conversation_keys(user_id, session_id)
    except Exception as exc:
        logger.warning(
            "会话已删除，但旧 Redis 对话 key 清理失败: error_type=%s",
            type(exc).__name__,
        )

    return {"detail": "删除成功"}
