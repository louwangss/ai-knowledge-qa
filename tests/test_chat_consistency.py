"""Chat 持久化与 Redis 状态一致性回归测试。"""

import asyncio
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_chat
from app.models.schemas import ChatRequest
from db.database import Base
from db.models import ChatHistory, Session as SessionModel, User
from memory.short_term import _key, append_message, cleanup_failed_turn, get_messages


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_sqlite_integer(type_, compiler, **kwargs):
    """让 MySQL BIGINT 主键在 SQLite 回归测试中保留自增语义。"""
    return "INTEGER"


class FakeRedis:
    """覆盖短期记忆本次测试所需命令的内存 Redis。"""

    def __init__(self):
        self.values = {}
        self.lists = {}

    def pipeline(self):
        return self

    def execute(self):
        return []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = str(value)
        return True

    def expire(self, key, ttl):
        return True

    def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    def decr(self, key):
        value = int(self.values.get(key, 0)) - 1
        self.values[key] = str(value)
        return value

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def rpop(self, key):
        values = self.lists.get(key, [])
        return values.pop() if values else None

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        normalized_start = start if start >= 0 else max(len(values) + start, 0)
        normalized_end = end if end >= 0 else len(values) + end
        return values[normalized_start:normalized_end + 1]

    def lrem(self, key, count, value):
        values = self.lists.get(key, [])
        for index, item in enumerate(values):
            if item == value:
                del values[index]
                return 1
        return 0

    def ltrim(self, key, start, end):
        self.lists[key] = self.lrange(key, start, end)
        return True

    def delete(self, key):
        removed = int(key in self.values or key in self.lists)
        self.values.pop(key, None)
        self.lists.pop(key, None)
        return removed


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    try:
        db.add(User(id="u1", username="test-user"))
        db.add(SessionModel(id="s1", user_id="u1"))
        db.add_all([
            ChatHistory(user_id="u1", session_id="s1", role="user", content="历史问题"),
            ChatHistory(user_id="u1", session_id="s1", role="assistant", content="历史回答"),
        ])
        db.commit()
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


async def _retrieve_context(**kwargs):
    return {
        "documents": [],
        "notes": [],
        "episodic_memory": [],
        "short_term_memory": {},
    }


async def _stream_answer(payload, context):
    yield {"type": "token", "content": "本次回答"}


async def _stream_failure(payload, context):
    if False:
        yield
    raise RuntimeError("generation failed")


async def _collect_chat(
    db,
    redis,
    record_event_side_effect=None,
    stream_factory=_stream_answer,
):
    payload = ChatRequest(user_id="u1", session_id="s1", message="本次问题", mode="normal")

    with ExitStack() as stack:
        stack.enter_context(patch.object(routes_chat, "get_redis", return_value=redis))
        stack.enter_context(patch.object(routes_chat, "retrieve_context", side_effect=_retrieve_context))
        stack.enter_context(patch.object(routes_chat, "_stream_normal", side_effect=stream_factory))
        stack.enter_context(patch.object(routes_chat, "maybe_compress"))
        stack.enter_context(
            patch.object(routes_chat, "record_event", side_effect=record_event_side_effect)
        )

        response = await routes_chat.chat(payload, db)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)


def test_cold_restore_does_not_duplicate_current_user_message(db_session):
    """Redis miss 时当前 user 消息只能恢复/追加一次。"""
    redis = FakeRedis()

    stream = asyncio.run(_collect_chat(db_session, redis))

    messages = get_messages(redis, "u1", "s1")
    current_user_messages = [
        item for item in messages
        if item["role"] == "user" and item["content"] == "本次问题"
    ]
    assert len(current_user_messages) == 1
    assert "event: done" in stream


def test_completed_answer_survives_auxiliary_persistence_failure(db_session):
    """assistant 已提交后，情景记忆失败不应回滚完整问答。"""
    redis = FakeRedis()

    stream = asyncio.run(
        _collect_chat(db_session, redis, record_event_side_effect=RuntimeError("episodic failed"))
    )

    rows = db_session.execute(
        select(ChatHistory).order_by(ChatHistory.id.asc())
    ).scalars().all()
    persisted = [(row.role, row.content) for row in rows]
    assert ("user", "本次问题") in persisted
    assert ("assistant", "本次回答") in persisted
    assert "event: done" in stream
    assert "event: error" not in stream


def test_cleanup_removes_exact_pending_message_instead_of_list_tail():
    """并发追加后，清理只能删除指定 message_id 的 pending user 消息。"""
    redis = FakeRedis()
    append_message(redis, "u1", "s1", "user", "待回滚问题", message_id="pending-1")
    append_message(redis, "u1", "s1", "user", "稍后到达的问题", message_id="pending-2")
    redis.set(_key("u1", "s1", "round_count"), 2)

    removed = cleanup_failed_turn(
        redis,
        "u1",
        "s1",
        message_id="pending-1",
        content="待回滚问题",
        decrement_round_count=True,
    )

    messages = get_messages(redis, "u1", "s1")
    assert removed is True
    assert [item["message_id"] for item in messages] == ["pending-2"]
    assert redis.get(_key("u1", "s1", "round_count")) == "1"


def test_cleanup_does_not_decrement_round_when_increment_never_succeeded():
    """user 已缓存但 INCR 失败时，回滚不能减少既有轮数。"""
    redis = FakeRedis()
    append_message(redis, "u1", "s1", "user", "待回滚问题", message_id="pending-1")
    redis.set(_key("u1", "s1", "round_count"), 3)

    removed = cleanup_failed_turn(
        redis,
        "u1",
        "s1",
        message_id="pending-1",
        content="待回滚问题",
        decrement_round_count=False,
    )

    assert removed is True
    assert get_messages(redis, "u1", "s1") == []
    assert redis.get(_key("u1", "s1", "round_count")) == "3"


def test_generation_failure_removes_pending_turn_and_does_not_leave_title(db_session):
    """回答生成失败时只保留历史，不留下当前消息或由它生成的标题。"""
    redis = FakeRedis()

    stream = asyncio.run(
        _collect_chat(db_session, redis, stream_factory=_stream_failure)
    )

    rows = db_session.execute(
        select(ChatHistory).order_by(ChatHistory.id.asc())
    ).scalars().all()
    assert [(row.role, row.content) for row in rows] == [
        ("user", "历史问题"),
        ("assistant", "历史回答"),
    ]
    assert [item["content"] for item in get_messages(redis, "u1", "s1")] == [
        "历史问题",
        "历史回答",
    ]
    session = db_session.get(SessionModel, "s1")
    assert session.title is None
    assert "event: error" in stream
