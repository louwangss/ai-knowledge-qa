"""Gradio 首屏所需数据的只读聚合接口。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_app_user
from app.models.schemas import BootstrapResponse
from db.models import ChatHistory, Document, SemanticMemory, Session as SessionModel


router = APIRouter(prefix="/api/v1/bootstrap", tags=["bootstrap"])


@router.get("", response_model=BootstrapResponse)
def get_bootstrap(
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """在一个响应中返回文档、笔记、会话和最近会话历史。"""
    require_app_user(user_id)
    sessions = db.query(SessionModel.id, SessionModel.title).filter(
        SessionModel.user_id == user_id,
        SessionModel.status == "active",
    ).order_by(SessionModel.last_active.desc()).all()
    documents = db.query(
        Document.id,
        Document.filename,
        Document.chunk_count,
        Document.created_at,
    ).filter(
        Document.user_id == user_id,
        Document.status == "ready",
    ).order_by(Document.created_at.desc()).all()
    notes = db.query(SemanticMemory.id, SemanticMemory.concept).filter(
        SemanticMemory.user_id == user_id,
    ).order_by(SemanticMemory.created_at.desc()).all()

    active_note = None
    if notes:
        active_note = db.query(
            SemanticMemory.id,
            SemanticMemory.concept,
            SemanticMemory.content,
        ).filter(
            SemanticMemory.id == notes[0].id,
            SemanticMemory.user_id == user_id,
        ).first()

    history = []
    if sessions:
        history = db.query(ChatHistory.role, ChatHistory.content).filter(
            ChatHistory.user_id == user_id,
            ChatHistory.session_id == sessions[0].id,
        ).order_by(ChatHistory.created_at.asc()).all()

    return {
        "documents": documents,
        "sessions": sessions,
        "notes": notes,
        "active_note": active_note,
        "history": history,
    }
