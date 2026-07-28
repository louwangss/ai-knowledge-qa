"""Pydantic 请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel


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
    concept: str
    content: str


class NoteUpdate(BaseModel):
    concept: str | None = None
    content: str | None = None


class NoteResponse(BaseModel):
    id: int
    user_id: str
    concept: str | None
    content: str
    created_at: datetime
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


class BootstrapMessage(BaseModel):
    role: str
    content: str

    class Config:
        from_attributes = True


class BootstrapResponse(BaseModel):
    documents: list[BootstrapDocument]
    sessions: list[BootstrapSession]
    notes: list[BootstrapNote]
    history: list[BootstrapMessage]
