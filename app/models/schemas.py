"""Pydantic 请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel, Field, computed_field, field_validator

from app.note_version import build_note_version


MAX_NOTE_CONTENT_BYTES = 65_535


def _validate_note_content_bytes(value: str | None) -> str | None:
    """MySQL TEXT 按字节限制容量，按 UTF-8 编码在 API 边界校验。"""
    if value is not None and len(value.encode("utf-8")) > MAX_NOTE_CONTENT_BYTES:
        raise ValueError("笔记正文过长")
    return value


# ---- User ----

class UserCreate(BaseModel):
    username: str


class UserResponse(BaseModel):
    id: str
    username: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Session ----

class SessionCreate(BaseModel):
    user_id: str


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: str | None
    status: str
    created_at: datetime
    last_active: datetime

    class Config:
        from_attributes = True


# ---- Document ----

class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    file_size: int | None
    chunk_count: int | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Chat ----

class ChatRequest(BaseModel):
    user_id: str
    session_id: str
    message: str
    mode: str = "normal"  # normal / deep


class ChatMessage(BaseModel):
    id: int
    role: str
    content: str
    mode: str | None
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Note ----

class NoteCreate(BaseModel):
    user_id: str
    concept: str = Field(default="", max_length=100)
    content: str = ""

    _validate_content = field_validator("content")(_validate_note_content_bytes)


class NoteUpdate(BaseModel):
    concept: str | None = Field(default=None, max_length=100)
    content: str | None = None
    version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    _validate_content = field_validator("content")(_validate_note_content_bytes)


class NoteResponse(BaseModel):
    id: int
    user_id: str
    concept: str | None
    content: str
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def version(self) -> str:
        return build_note_version(self.concept, self.content)

    class Config:
        from_attributes = True


class NoteSummary(BaseModel):
    id: int
    concept: str | None
    updated_at: datetime

    class Config:
        from_attributes = True


# ---- Bootstrap ----

class BootstrapDocument(BaseModel):
    id: str
    filename: str
    chunk_count: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class BootstrapSession(BaseModel):
    id: str
    title: str | None

    class Config:
        from_attributes = True


class BootstrapNote(BaseModel):
    id: int
    concept: str | None

    class Config:
        from_attributes = True


class BootstrapActiveNote(BaseModel):
    id: int
    concept: str | None
    content: str

    class Config:
        from_attributes = True


class BootstrapMessage(BaseModel):
    role: str
    content: str

    class Config:
        from_attributes = True


class BootstrapResponse(BaseModel):
    documents: list[BootstrapDocument]
    sessions: list[BootstrapSession]
    notes: list[BootstrapNote]
    active_note: BootstrapActiveNote | None = None
    history: list[BootstrapMessage]
