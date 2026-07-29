"""会话删除完整性与 session-user 归属校验。"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_chat, routes_sessions
from app.models.schemas import ChatRequest
from db import models as db_models
from db.init_db import INDEX_SQL
from db.database import Base
from db.models import (
    ChatHistory,
    ChatTurn,
    EpisodicMemory,
    Session as SessionModel,
    SessionSummary,
    User,
)


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
            ChatTurn(
                client_turn_id="00000000-0000-4000-8000-000000000001",
                user_id="u1",
                session_id="s1",
                request_fingerprint="a" * 64,
                status="processing",
            ),
        ])
        db.commit()
        yield db
    finally:
        db.rollback()
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def legacy_redis_cleanup(monkeypatch):
    cleanup = MagicMock()
    monkeypatch.setattr(routes_sessions, "delete_legacy_conversation_keys", cleanup)
    return cleanup


def test_delete_session_removes_all_database_relations(db_session, legacy_redis_cleanup):
    """删除会话应在同一数据库事务中清除全部关联表。"""
    response = routes_sessions.delete_session("s1", user_id="u1", db=db_session)

    assert response == {"detail": "删除成功"}
    assert db_session.get(SessionModel, "s1") is None
    assert db_session.execute(select(ChatHistory)).scalars().all() == []
    assert db_session.execute(select(EpisodicMemory)).scalars().all() == []
    assert db_session.execute(select(SessionSummary)).scalars().all() == []
    assert db_session.execute(select(ChatTurn)).scalars().all() == []
    legacy_redis_cleanup.assert_called_once_with("u1", "s1")


def test_delete_session_removes_persisted_chat_sources(db_session, legacy_redis_cleanup):
    """删除会话时应先清除回答关联的结构化来源。"""
    chat_source_model = getattr(db_models, "ChatSource")
    db_session.add(chat_source_model(
        id=1,
        message_id=1,
        source="架构说明.md",
        score=0.25,
        position=0,
    ))
    db_session.commit()

    routes_sessions.delete_session("s1", user_id="u1", db=db_session)

    assert db_session.execute(select(chat_source_model)).scalars().all() == []


def test_chat_sources_lookup_index_is_declared():
    """初始化脚本应为 history 聚合来源声明稳定的查询索引。"""
    assert any(
        "chat_sources(message_id, position)" in statement
        for statement in INDEX_SQL
    )


def test_other_user_cannot_delete_session(db_session, legacy_redis_cleanup):
    """非应用用户删除会话时返回 403 且不修改任何状态。"""
    with pytest.raises(HTTPException) as exc_info:
        routes_sessions.delete_session("s1", user_id="u2", db=db_session)

    assert exc_info.value.status_code == 403
    assert db_session.get(SessionModel, "s1") is not None
    assert db_session.execute(select(SessionSummary)).scalars().one().summary == "早期摘要"
    legacy_redis_cleanup.assert_not_called()


def test_legacy_redis_cleanup_failure_does_not_undo_database_delete(
    db_session,
    legacy_redis_cleanup,
):
    """历史派生 key 清理失败不能回滚已完成的权威数据库删除。"""
    legacy_redis_cleanup.side_effect = RuntimeError("redis unavailable")

    response = routes_sessions.delete_session("s1", user_id="u1", db=db_session)

    assert response == {"detail": "删除成功"}
    assert db_session.get(SessionModel, "s1") is None


def test_other_user_cannot_read_session_history(db_session):
    """历史记录接口必须先拒绝非应用用户。"""
    with pytest.raises(HTTPException) as exc_info:
        routes_chat.get_chat_history(user_id="u2", session_id="s1", db=db_session)

    assert exc_info.value.status_code == 403


def test_history_uses_message_id_as_stable_tiebreaker(db_session):
    """同一时间戳的消息必须按自增 ID 稳定返回。"""
    same_time = datetime(2026, 7, 29, 10, 0, 0)
    first = db_session.get(ChatHistory, 1)
    first.created_at = same_time
    db_session.add_all([
        ChatHistory(
            id=3,
            user_id="u1",
            session_id="s1",
            role="assistant",
            content="第三条",
            created_at=same_time,
        ),
        ChatHistory(
            id=2,
            user_id="u1",
            session_id="s1",
            role="user",
            content="第二条",
            created_at=same_time,
        ),
    ])
    db_session.commit()

    history = routes_chat.get_chat_history(user_id="u1", session_id="s1", db=db_session)

    assert [message.id for message in history] == [1, 2, 3]


def test_session_list_has_stable_order_when_activity_timestamps_match(db_session):
    """last_active 相同时使用 created_at 和 ID 确定稳定顺序。"""
    same_time = datetime(2026, 7, 29, 10, 0, 0)
    first = db_session.get(SessionModel, "s1")
    first.created_at = same_time
    first.last_active = same_time
    db_session.add_all([
        SessionModel(id="s2", user_id="u1", created_at=same_time, last_active=same_time),
        SessionModel(id="s3", user_id="u1", created_at=same_time, last_active=same_time),
    ])
    db_session.commit()

    sessions = routes_sessions.list_sessions(user_id="u1", db=db_session)

    assert [session.id for session in sessions] == ["s3", "s2", "s1"]


def test_other_user_cannot_start_chat_in_session(db_session):
    """Chat 入口不能把用户消息写入其他用户的 session。"""
    payload = ChatRequest(user_id="u2", session_id="s1", message="越权问题")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes_chat.chat(payload, db_session))

    assert exc_info.value.status_code == 403
    rows = db_session.execute(select(ChatHistory)).scalars().all()
    assert [(row.user_id, row.content) for row in rows] == [("u1", "问题")]
