"""会话路由：POST 创建、GET 列表、DELETE 删除"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.schemas import SessionCreate, SessionResponse
from db.models import Session as SessionModel, ChatHistory, EpisodicMemory, User

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse)
def create_session(payload: SessionCreate, db: Session = Depends(get_db)):
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
    return db.query(SessionModel).filter(
        SessionModel.user_id == user_id,
        SessionModel.status == "active",
    ).order_by(SessionModel.last_active.desc()).all()


@router.delete("/{session_id}")
def delete_session(session_id: str, user_id: str = Query(...), db: Session = Depends(get_db)):
    """硬删除会话 + 关联的 chat_history + episodic_memory"""
    session = db.query(SessionModel).filter(
        SessionModel.id == session_id,
        SessionModel.user_id == user_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    db.query(ChatHistory).filter(ChatHistory.session_id == session_id).delete()
    db.query(EpisodicMemory).filter(EpisodicMemory.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return {"detail": "删除成功"}
