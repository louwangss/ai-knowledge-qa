"""会话删除完整性与 session-user 归属校验。"""

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_chat, routes_sessions
from app.models.schemas import ChatRequest
from db.database import Base
from db.models import (
    ChatHistory,
    EpisodicMemory,
    Session as SessionModel,
    SessionSummary,
    User,
)
from memory.short_term import _key


class FakeRedis:
    def __init__(self):
        self.values = {}

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.values:
                removed += 1
                del self.values[key]
        return removed


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    try:
        db.add_all([
            User(id="u1", username="owner"),
            User(id="u2", username="other"),
            SessionModel(id="s1", user_id="u1"),
        ])
        db.commit()
        db.add_all([
            ChatHistory(
                id=1,
                user_id="u1",
                session_id="s1",
                role="user",
                content="问题",
            ),
            EpisodicMemory(
                id=1,
                user_id="u1",
                session_id="s1",
                event_type="qa_completed",
                content="完成问答",
            ),
            SessionSummary(
                id=1,
                session_id="s1",
                summary="早期摘要",
                compressed_count=2,
            ),
        ])
        db.commit()
        yield db
    finally:
        db.rollback()
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_delete_session_removes_summary_relations_and_redis_state(db_session, monkeypatch):
    """删除会话应同时清除全部关联表和四类 Redis 状态。"""
    redis = FakeRedis()
    session_keys = [
        _key("u1", "s1", suffix)
        for suffix in ["summary", "messages", "round_count", "next_compress_round"]
    ]
    for key in session_keys:
        redis.values[key] = "value"
    redis.values["unrelated"] = "keep"
    monkeypatch.setattr(routes_sessions, "get_redis", lambda: redis, raising=False)

    response = routes_sessions.delete_session("s1", user_id="u1", db=db_session)

    assert response == {"detail": "删除成功"}
    assert db_session.get(SessionModel, "s1") is None
    assert db_session.execute(select(ChatHistory)).scalars().all() == []
    assert db_session.execute(select(EpisodicMemory)).scalars().all() == []
    assert db_session.execute(select(SessionSummary)).scalars().all() == []
    assert all(key not in redis.values for key in session_keys)
    assert redis.values["unrelated"] == "keep"


def test_other_user_cannot_delete_session(db_session, monkeypatch):
    """非所属用户删除会话时返回 404 且不修改任何状态。"""
    redis = FakeRedis()
    monkeypatch.setattr(routes_sessions, "get_redis", lambda: redis, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        routes_sessions.delete_session("s1", user_id="u2", db=db_session)

    assert exc_info.value.status_code == 404
    assert db_session.get(SessionModel, "s1") is not None
    assert db_session.execute(select(SessionSummary)).scalars().one().summary == "早期摘要"


def test_other_user_cannot_read_session_history(db_session):
    """历史记录接口必须先验证 session 属于请求用户。"""
    with pytest.raises(HTTPException) as exc_info:
        routes_chat.get_chat_history(user_id="u2", session_id="s1", db=db_session)

    assert exc_info.value.status_code == 404


def test_other_user_cannot_start_chat_in_session(db_session):
    """Chat 入口不能把用户消息写入其他用户的 session。"""
    payload = ChatRequest(user_id="u2", session_id="s1", message="越权问题")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes_chat.chat(payload, db_session))

    assert exc_info.value.status_code == 404
    rows = db_session.execute(select(ChatHistory)).scalars().all()
    assert [(row.user_id, row.content) for row in rows] == [("u1", "问题")]
