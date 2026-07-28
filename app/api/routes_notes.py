"""笔记路由：CRUD"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_app_user
from app.models.schemas import NoteCreate, NoteResponse, NoteSummary, NoteUpdate
from db.models import User
from memory.semantic import (
    create_note,
    delete_note,
    get_note,
    get_note_summaries,
    get_notes,
    sync_note_index_task,
    update_note,
    NoteVersionConflictError,
)

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


@router.post("", response_model=NoteResponse)
def create_note_endpoint(
    payload: NoteCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    require_app_user(payload.user_id)
    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    note = create_note(db, payload.user_id, payload.concept, payload.content)
    if payload.content.strip():
        background_tasks.add_task(sync_note_index_task, note.id, payload.user_id)
    return note


@router.get("", response_model=list[NoteResponse])
def list_notes(user_id: str = Query(...), db: Session = Depends(get_db)):
    require_app_user(user_id)
    return get_notes(db, user_id)


@router.get("/summaries", response_model=list[NoteSummary])
def list_note_summaries(user_id: str = Query(...), db: Session = Depends(get_db)):
    require_app_user(user_id)
    return get_note_summaries(db, user_id)


@router.get("/{note_id}", response_model=NoteResponse)
def get_note_endpoint(
    note_id: int,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    require_app_user(user_id)
    note = get_note(db, note_id, user_id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return note


@router.put("/{note_id}", response_model=NoteResponse)
def update_note_endpoint(
    note_id: int,
    payload: NoteUpdate,
    background_tasks: BackgroundTasks,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    require_app_user(user_id)
    try:
        note = update_note(
            db,
            note_id,
            user_id,
            payload.concept,
            payload.content,
            expected_version=payload.version,
        )
    except NoteVersionConflictError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NOTE_VERSION_CONFLICT",
                "message": "笔记已在其他位置更新",
            },
        )
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    background_tasks.add_task(sync_note_index_task, note.id, user_id)
    return note


@router.delete("/{note_id}")
def delete_note_endpoint(note_id: int, user_id: str = Query(...), db: Session = Depends(get_db)):
    require_app_user(user_id)
    success = delete_note(db, note_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return {"detail": "删除成功"}
