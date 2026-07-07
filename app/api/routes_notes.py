"""笔记路由：CRUD"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.schemas import NoteCreate, NoteUpdate, NoteResponse
from db.models import User
from memory.semantic import create_note, update_note, delete_note, get_notes

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


@router.post("", response_model=NoteResponse)
def create_note_endpoint(payload: NoteCreate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    note, is_duplicate = create_note(db, payload.user_id, payload.concept, payload.content)
    if is_duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"已有类似笔记: {note.concept}",
        )
    return note


@router.get("", response_model=list[NoteResponse])
def list_notes(user_id: str = Query(...), db: Session = Depends(get_db)):
    return get_notes(db, user_id)


@router.put("/{note_id}", response_model=NoteResponse)
def update_note_endpoint(
    note_id: int,
    payload: NoteUpdate,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    note = update_note(db, note_id, user_id, payload.concept, payload.content)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return note


@router.delete("/{note_id}")
def delete_note_endpoint(note_id: int, user_id: str = Query(...), db: Session = Depends(get_db)):
    success = delete_note(db, note_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return {"detail": "删除成功"}
