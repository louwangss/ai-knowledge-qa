"""Chat 幂等状态机、原子持久化与来源一致性回归测试。"""

import asyncio
import json
import logging
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import BigInteger, create_engine, event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_chat
from app.models.schemas import ChatMessage, ChatRequest
from db.database import Base
from db.models import ChatHistory, ChatSource, ChatTurn, Session as SessionModel, User
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


async def _stream_answer_with_sources(payload, context):
    yield {"type": "token", "content": "本次回答"}
    yield {
        "type": "sources",
        "content": [{"source": "架构说明.md", "score": 0.125}],
    }


async def _stream_answer_with_untrusted_sources(payload, context):
    yield {"type": "token", "content": "本次回答"}
    yield {
        "type": "sources",
        "content": [
            {"source": "  架构说明.md  ", "score": 0.8},
            {"source": "架构说明.md", "score": 0.1},
            {"source": "异常分数", "score": float("nan")},
            {"source": "x" * (routes_chat.CHAT_SOURCE_MAX_LENGTH + 5), "score": "invalid"},
            {"source": "", "score": 1},
            {"source": None, "score": 1},
            "not-a-source",
        ],
    }


async def _stream_failure(payload, context):
    if False:
        yield
    raise RuntimeError("generation failed")


async def _stream_empty(payload, context):
    if False:
        yield


async def _stream_cancelled(payload, context):
    if False:
        yield
    raise asyncio.CancelledError()


async def _consume_response(response, *, run_background=False):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    if run_background and response.background is not None:
        await response.background()
    return "".join(chunks)


async def _collect_chat(
    db,
    redis,
    record_event_side_effect=None,
    stream_factory=_stream_answer,
    message="本次问题",
    mode="normal",
    client_turn_id=None,
    compression_side_effect=None,
    retrieve_factory=_retrieve_context,
):
    payload = ChatRequest(
        user_id="u1",
        session_id="s1",
        message=message,
        mode=mode,
        client_turn_id=client_turn_id,
    )

    with ExitStack() as stack:
        stack.enter_context(patch.object(routes_chat, "retrieve_context", side_effect=retrieve_factory))
        stream_name = "_stream_deep" if mode == "deep" else "_stream_normal"
        stack.enter_context(patch.object(routes_chat, stream_name, side_effect=stream_factory))
        stack.enter_context(patch.object(
            routes_chat,
            "maybe_compress_conversation",
            side_effect=compression_side_effect,
        ))
        stack.enter_context(
            patch.object(routes_chat, "record_event", side_effect=record_event_side_effect)
        )
        stack.enter_context(patch.object(
            routes_chat,
            "SessionLocal",
            sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False),
        ))

        response = await routes_chat.chat(payload, db)
        return await _consume_response(response, run_background=True)


def test_completed_turn_persists_one_atomic_message_pair(db_session):
    """一次成功生成只追加一对最终消息，Redis 不再承载权威写入。"""
    redis = FakeRedis()

    stream = asyncio.run(_collect_chat(db_session, redis))

    rows = db_session.execute(
        select(ChatHistory).where(ChatHistory.content.in_(["本次问题", "本次回答"]))
    ).scalars().all()
    assert [(row.role, row.content) for row in rows] == [
        ("user", "本次问题"),
        ("assistant", "本次回答"),
    ]
    assert get_messages(redis, "u1", "s1") == []
    assert "event: done" in stream


def test_chat_request_rejects_unknown_mode():
    """API 边界只能接受 normal/deep，避免前后端进入不同分支。"""
    with pytest.raises(ValidationError):
        ChatRequest(user_id="u1", session_id="s1", message="问题", mode="unexpected")


def test_chat_request_accepts_optional_uuid_turn_id():
    legacy = ChatRequest(user_id="u1", session_id="s1", message="问题")
    turn_id = uuid.uuid4()
    current = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="问题",
        client_turn_id=str(turn_id),
    )

    assert legacy.client_turn_id is None
    assert current.client_turn_id == turn_id


def test_chat_request_rejects_non_uuid_turn_id():
    with pytest.raises(ValidationError):
        ChatRequest(
            user_id="u1",
            session_id="s1",
            message="问题",
            client_turn_id="not-a-uuid",
        )


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


def test_completed_answer_survives_conversation_compression_failure(db_session):
    """摘要压缩属于派生状态，失败不能回滚已完成问答。"""
    attempted = []

    def fail_compression(db, user_id, session_id):
        attempted.append((user_id, session_id))
        raise RuntimeError("compression failed")

    stream = asyncio.run(
        _collect_chat(
            db_session,
            FakeRedis(),
            compression_side_effect=fail_compression,
        )
    )

    assert attempted == [("u1", "s1")]
    assert "event: done" in stream
    assert db_session.execute(
        select(ChatHistory).where(ChatHistory.content == "本次回答")
    ).scalars().one().role == "assistant"


def test_derived_state_runs_after_done_as_response_background_task(db_session):
    """摘要 LLM 不得阻塞 SSE 的 sources/done，也不得占用请求 Session。"""
    attempted = []
    session_factory = sessionmaker(
        bind=db_session.get_bind(),
        autocommit=False,
        autoflush=False,
    )

    def record_compression(db, user_id, session_id):
        attempted.append((db is db_session, user_id, session_id))

    async def run():
        payload = ChatRequest(
            user_id="u1",
            session_id="s1",
            message="后台派生状态",
            mode="normal",
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(routes_chat, "retrieve_context", side_effect=_retrieve_context))
            stack.enter_context(patch.object(routes_chat, "_stream_normal", side_effect=_stream_answer))
            stack.enter_context(patch.object(routes_chat, "SessionLocal", session_factory))
            stack.enter_context(patch.object(
                routes_chat,
                "maybe_compress_conversation",
                side_effect=record_compression,
            ))
            stack.enter_context(patch.object(routes_chat, "record_event"))

            response = await routes_chat.chat(payload, db_session)
            stream = await _consume_response(response)
            assert "event: done" in stream
            assert attempted == []
            assert response.background is not None
            await response.background()

    asyncio.run(run())
    assert attempted == [(False, "u1", "s1")]


def test_stream_cancels_heartbeat_task_after_completion(db_session):
    """响应结束后必须回收心跳任务，避免每轮问答泄漏后台协程。"""
    heartbeat_events = []

    async def fake_heartbeat(turn_id, lease_owner, lease_lost):
        heartbeat_events.append(("started", turn_id, lease_owner))
        try:
            await asyncio.Event().wait()
        finally:
            heartbeat_events.append(("stopped", turn_id, lease_owner))

    async def yielding_retrieve(**kwargs):
        await asyncio.sleep(0)
        return await _retrieve_context(**kwargs)

    async def run():
        payload = ChatRequest(
            user_id="u1",
            session_id="s1",
            message="心跳回收",
            mode="normal",
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                routes_chat,
                "retrieve_context",
                side_effect=yielding_retrieve,
            ))
            stack.enter_context(patch.object(
                routes_chat,
                "_stream_normal",
                side_effect=_stream_answer,
            ))
            stack.enter_context(patch.object(
                routes_chat,
                "_chat_turn_heartbeat",
                side_effect=fake_heartbeat,
            ))
            response = await routes_chat.chat(payload, db_session)
            return await _consume_response(response)

    stream = asyncio.run(run())

    assert "event: done" in stream
    assert [event[0] for event in heartbeat_events] == ["started", "stopped"]
    assert heartbeat_events[0][1:] == heartbeat_events[1][1:]


def test_lost_lease_stops_generation_without_persisting_history(db_session):
    """心跳确认租约丢失后，旧执行流不得写入完整问答。"""
    turn_id = uuid.uuid4()

    async def lose_lease(current_turn_id, lease_owner, lease_lost):
        lease_lost.set()

    async def yielding_retrieve(**kwargs):
        await asyncio.sleep(0)
        return await _retrieve_context(**kwargs)

    async def run():
        payload = ChatRequest(
            user_id="u1",
            session_id="s1",
            message="租约丢失问题",
            mode="normal",
            client_turn_id=turn_id,
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                routes_chat,
                "retrieve_context",
                side_effect=yielding_retrieve,
            ))
            stack.enter_context(patch.object(
                routes_chat,
                "_stream_normal",
                side_effect=_stream_answer,
            ))
            stack.enter_context(patch.object(
                routes_chat,
                "_chat_turn_heartbeat",
                side_effect=lose_lease,
            ))
            response = await routes_chat.chat(payload, db_session)
            return await _consume_response(response)

    stream = asyncio.run(run())

    assert "event: error" in stream
    assert db_session.get(ChatTurn, str(turn_id)).status == "failed"
    assert db_session.execute(
        select(ChatHistory).where(ChatHistory.content == "租约丢失问题")
    ).scalars().all() == []


def test_completed_answer_persists_mode_and_sources_for_history(db_session):
    """完成回答的模式和结构化来源应可从 history 原样恢复。"""
    stream = asyncio.run(
        _collect_chat(
            db_session,
            FakeRedis(),
            stream_factory=_stream_answer_with_sources,
        )
    )

    assistant = db_session.execute(
        select(ChatHistory)
        .where(ChatHistory.role == "assistant", ChatHistory.content == "本次回答")
    ).scalars().one()
    assert assistant.mode == "normal"
    assert [(item.source, item.score) for item in assistant.sources] == [
        ("架构说明.md", 0.125),
    ]

    history = routes_chat.get_chat_history(user_id="u1", session_id="s1", db=db_session)
    serialized = [ChatMessage.model_validate(item).model_dump() for item in history]
    restored_answer = next(item for item in serialized if item["content"] == "本次回答")
    assert restored_answer["sources"] == [{"source": "架构说明.md", "score": 0.125}]
    assert "event: sources" in stream


def test_deep_answer_persists_mode_and_sources(db_session):
    """deep 分支也必须把其 sources 与 assistant 一起持久化。"""
    asyncio.run(
        _collect_chat(
            db_session,
            FakeRedis(),
            stream_factory=_stream_answer_with_sources,
            mode="deep",
        )
    )

    assistant = db_session.execute(
        select(ChatHistory)
        .where(ChatHistory.role == "assistant", ChatHistory.content == "本次回答")
    ).scalars().one()
    assert assistant.mode == "deep"
    assert [(item.source, item.score) for item in assistant.sources] == [
        ("架构说明.md", 0.125),
    ]


def test_sources_are_normalized_once_for_stream_and_history(db_session):
    """外部来源先规范化，再以同一列表写入 SSE 和历史。"""
    stream = asyncio.run(
        _collect_chat(
            db_session,
            FakeRedis(),
            stream_factory=_stream_answer_with_untrusted_sources,
        )
    )

    expected = [
        {"source": "架构说明.md", "score": 0.8},
        {"source": "异常分数", "score": 0.0},
        {"source": "x" * routes_chat.CHAT_SOURCE_MAX_LENGTH, "score": 0.0},
    ]
    assistant = db_session.execute(
        select(ChatHistory)
        .where(ChatHistory.role == "assistant", ChatHistory.content == "本次回答")
    ).scalars().one()

    assert [{"source": item.source, "score": item.score} for item in assistant.sources] == expected
    assert f"data: {json.dumps({'content': expected}, ensure_ascii=False)}" in stream
    assert "event: done" in stream


def test_source_normalization_has_a_bounded_stable_prefix():
    """来源数量上限沿用 deep 模式 3~5 个子问题、每题 3 条的设计边界。"""
    raw_sources = [
        {"source": f"source-{index}", "score": index / 100}
        for index in range(routes_chat.CHAT_SOURCE_MAX_COUNT + 2)
    ]

    normalized = routes_chat._normalize_sources(raw_sources)

    assert len(normalized) == routes_chat.CHAT_SOURCE_MAX_COUNT
    assert [item["source"] for item in normalized] == [
        f"source-{index}" for index in range(routes_chat.CHAT_SOURCE_MAX_COUNT)
    ]


def test_turn_reservation_is_committed_before_streaming_starts(db_session):
    """返回 StreamingResponse 前必须持久化 processing reservation。"""
    turn_id = uuid.uuid4()
    payload = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="尚未消费响应体",
        client_turn_id=turn_id,
    )

    response = asyncio.run(routes_chat.chat(payload, db_session))

    reserved = db_session.get(ChatTurn, str(turn_id))
    assert reserved is not None
    assert reserved.status == "processing"
    assert uuid.UUID(reserved.lease_owner)
    assert reserved.lease_expires_at > datetime.utcnow()
    assert reserved.user_message_id is None
    assert reserved.assistant_message_id is None
    assert response.media_type == "text/event-stream"
    assert response.headers["X-Chat-Turn-ID"] == str(turn_id)


def test_legacy_request_receives_server_generated_turn_id_header(db_session):
    payload = ChatRequest(user_id="u1", session_id="s1", message="旧客户端问题")

    response = asyncio.run(routes_chat.chat(payload, db_session))

    generated = uuid.UUID(response.headers["X-Chat-Turn-ID"])
    assert db_session.get(ChatTurn, str(generated)).status == "processing"


def test_same_processing_turn_returns_structured_conflict(db_session):
    turn_id = uuid.uuid4()
    payload = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="重复请求",
        client_turn_id=turn_id,
    )

    asyncio.run(routes_chat.chat(payload, db_session))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes_chat.chat(payload, db_session))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "TURN_IN_PROGRESS",
        "message": "该请求正在处理中",
    }


def test_expired_processing_turn_can_be_reclaimed_with_fresh_owner(db_session):
    """worker 中断后同一幂等键可在租约过期时由新执行尝试接管。"""
    turn_id = uuid.uuid4()
    payload = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="过期后接管",
        client_turn_id=turn_id,
    )

    asyncio.run(routes_chat.chat(payload, db_session))
    turn = db_session.get(ChatTurn, str(turn_id))
    old_owner = turn.lease_owner
    turn.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    asyncio.run(routes_chat.chat(payload, db_session))
    db_session.refresh(turn)

    assert turn.status == "processing"
    assert turn.lease_owner != old_owner
    assert uuid.UUID(turn.lease_owner)
    assert turn.lease_expires_at > datetime.utcnow()


def test_old_lease_owner_cannot_complete_or_fail_reclaimed_turn(db_session):
    """每次接管使用新 fencing token，旧 worker 不能覆盖新 worker。"""
    turn_id = uuid.uuid4()
    payload = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="所有者隔离",
        client_turn_id=turn_id,
    )

    asyncio.run(routes_chat.chat(payload, db_session))
    turn = db_session.get(ChatTurn, str(turn_id))
    old_owner = turn.lease_owner
    turn.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    asyncio.run(routes_chat.chat(payload, db_session))
    db_session.refresh(turn)
    new_owner = turn.lease_owner

    assert routes_chat._mark_turn_failed(db_session, str(turn_id), old_owner) is False
    with pytest.raises(RuntimeError, match="租约"):
        routes_chat._persist_completed_turn(
            db_session,
            payload,
            str(turn_id),
            old_owner,
            "旧 worker 回答",
            [],
        )
    db_session.refresh(turn)
    assert turn.status == "processing"
    assert turn.lease_owner == new_owner
    assert db_session.execute(
        select(ChatHistory).where(ChatHistory.content == "旧 worker 回答")
    ).scalars().all() == []


def test_heartbeat_only_renews_current_live_owner(db_session):
    turn_id = uuid.uuid4()
    payload = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="心跳续租",
        client_turn_id=turn_id,
    )
    asyncio.run(routes_chat.chat(payload, db_session))
    turn = db_session.get(ChatTurn, str(turn_id))
    owner = turn.lease_owner
    turn.lease_expires_at = datetime.utcnow() + timedelta(seconds=5)
    db_session.commit()
    previous_expiry = turn.lease_expires_at

    assert routes_chat._renew_chat_turn_lease(db_session, str(turn_id), "wrong-owner") is False
    assert routes_chat._renew_chat_turn_lease(db_session, str(turn_id), owner) is True
    db_session.refresh(turn)
    assert turn.lease_expires_at > previous_expiry

    turn.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert routes_chat._renew_chat_turn_lease(db_session, str(turn_id), owner) is False

    turn.status = "completed"
    turn.lease_expires_at = datetime.utcnow() + timedelta(minutes=1)
    db_session.commit()
    assert routes_chat._renew_chat_turn_lease(db_session, str(turn_id), owner) is False


def test_reused_turn_id_with_different_request_returns_structured_conflict(db_session):
    turn_id = uuid.uuid4()
    first = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="原始问题",
        client_turn_id=turn_id,
    )
    reused = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="不同问题",
        client_turn_id=turn_id,
    )

    asyncio.run(routes_chat.chat(first, db_session))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes_chat.chat(reused, db_session))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "TURN_ID_REUSED",
        "message": "client_turn_id 已用于其他请求",
    }


def test_turn_fingerprint_covers_user_session_mode_and_message():
    base = ChatRequest(user_id="u1", session_id="s1", message="问题", mode="normal")
    variants = [
        base,
        ChatRequest(user_id="u2", session_id="s1", message="问题", mode="normal"),
        ChatRequest(user_id="u1", session_id="s2", message="问题", mode="normal"),
        ChatRequest(user_id="u1", session_id="s1", message="问题", mode="deep"),
        ChatRequest(user_id="u1", session_id="s1", message="不同问题", mode="normal"),
    ]

    fingerprints = {routes_chat._request_fingerprint(item) for item in variants}

    assert len(fingerprints) == len(variants)
    assert all(len(item) == 64 for item in fingerprints)


def test_completed_turn_replays_answer_and_sources_without_generation(db_session):
    turn_id = uuid.uuid4()
    payload = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="可重放问题",
        client_turn_id=turn_id,
    )

    with ExitStack() as stack:
        retrieve_mock = stack.enter_context(
            patch.object(routes_chat, "retrieve_context", side_effect=_retrieve_context)
        )
        stream_mock = stack.enter_context(
            patch.object(routes_chat, "_stream_normal", side_effect=_stream_answer_with_sources)
        )
        stack.enter_context(patch.object(routes_chat, "maybe_compress_conversation"))
        stack.enter_context(patch.object(routes_chat, "record_event"))

        async def run_twice():
            first = await routes_chat.chat(payload, db_session)
            first_stream = await _consume_response(first)
            second = await routes_chat.chat(payload, db_session)
            second_stream = await _consume_response(second)
            return first_stream, second_stream

        first_stream, replay_stream = asyncio.run(run_twice())

    assert retrieve_mock.call_count == 1
    assert stream_mock.call_count == 1
    assert "本次回答" in first_stream
    assert "本次回答" in replay_stream
    assert "架构说明.md" in replay_stream
    rows = db_session.execute(
        select(ChatHistory).where(ChatHistory.content.in_(["可重放问题", "本次回答"]))
    ).scalars().all()
    assert [(row.role, row.content) for row in rows] == [
        ("user", "可重放问题"),
        ("assistant", "本次回答"),
    ]


def test_failed_turn_can_be_reclaimed_without_leaving_half_history(db_session):
    turn_id = uuid.uuid4()
    failed_stream = asyncio.run(
        _collect_chat(
            db_session,
            FakeRedis(),
            stream_factory=_stream_failure,
            message="失败后重试",
            client_turn_id=turn_id,
        )
    )

    turn = db_session.get(ChatTurn, str(turn_id))
    assert turn.status == "failed"
    assert turn.user_message_id is None
    assert turn.assistant_message_id is None
    assert "event: error" in failed_stream
    assert db_session.execute(
        select(ChatHistory).where(ChatHistory.content == "失败后重试")
    ).scalars().all() == []

    completed_stream = asyncio.run(
        _collect_chat(
            db_session,
            FakeRedis(),
            message="失败后重试",
            client_turn_id=turn_id,
        )
    )
    db_session.refresh(turn)
    assert turn.status == "completed"
    assert "event: done" in completed_stream
    assert len(db_session.execute(
        select(ChatHistory).where(ChatHistory.content == "失败后重试")
    ).scalars().all()) == 1


def test_cancelled_turn_is_failed_without_half_history(db_session):
    turn_id = uuid.uuid4()

    async def run_cancelled():
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(routes_chat, "retrieve_context", side_effect=_retrieve_context)
            )
            stack.enter_context(
                patch.object(routes_chat, "_stream_normal", side_effect=_stream_cancelled)
            )
            response = await routes_chat.chat(
                ChatRequest(
                    user_id="u1",
                    session_id="s1",
                    message="取消问题",
                    client_turn_id=turn_id,
                ),
                db_session,
            )
            await _consume_response(response)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_cancelled())

    assert db_session.get(ChatTurn, str(turn_id)).status == "failed"
    assert db_session.execute(
        select(ChatHistory).where(ChatHistory.content == "取消问题")
    ).scalars().all() == []


def test_failed_status_write_is_retried_from_generator_finally(db_session):
    turn_id = uuid.uuid4()
    original_mark_failed = routes_chat._mark_turn_failed
    attempts = []

    def fail_once(db, current_turn_id, lease_owner):
        attempts.append(current_turn_id)
        if len(attempts) == 1:
            return False
        return original_mark_failed(db, current_turn_id, lease_owner)

    async def run_failure():
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(routes_chat, "retrieve_context", side_effect=_retrieve_context)
            )
            stack.enter_context(
                patch.object(routes_chat, "_stream_normal", side_effect=_stream_failure)
            )
            stack.enter_context(
                patch.object(routes_chat, "_mark_turn_failed", side_effect=fail_once)
            )
            response = await routes_chat.chat(
                ChatRequest(
                    user_id="u1",
                    session_id="s1",
                    message="失败状态重试",
                    client_turn_id=turn_id,
                ),
                db_session,
            )
            return await _consume_response(response)

    stream = asyncio.run(run_failure())

    assert "event: error" in stream
    assert attempts == [str(turn_id), str(turn_id)]
    assert db_session.get(ChatTurn, str(turn_id)).status == "failed"


def test_persistence_failure_rolls_back_whole_turn_and_marks_failed(db_session):
    turn_id = uuid.uuid4()
    original_title = db_session.get(SessionModel, "s1").title
    original_last_active = db_session.get(SessionModel, "s1").last_active

    def fail_source_flush(session, flush_context, instances):
        if any(isinstance(item, ChatSource) for item in session.new):
            raise RuntimeError("source insert failed")

    event.listen(db_session, "before_flush", fail_source_flush)
    try:
        stream = asyncio.run(
            _collect_chat(
                db_session,
                FakeRedis(),
                stream_factory=_stream_answer_with_sources,
                message="事务失败问题",
                client_turn_id=turn_id,
            )
        )
    finally:
        event.remove(db_session, "before_flush", fail_source_flush)

    assert "event: error" in stream
    assert db_session.get(ChatTurn, str(turn_id)).status == "failed"
    assert db_session.execute(
        select(ChatHistory).where(ChatHistory.content.in_(["事务失败问题", "本次回答"]))
    ).scalars().all() == []
    session = db_session.get(SessionModel, "s1")
    assert session.title == original_title
    assert session.last_active == original_last_active


def test_chat_completion_does_not_require_redis(db_session):
    payload = ChatRequest(user_id="u1", session_id="s1", message="无 Redis 问题")

    async def run_without_redis():
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                routes_chat,
                "get_redis",
                side_effect=RuntimeError("redis unavailable"),
                create=True,
            ))
            stack.enter_context(
                patch.object(routes_chat, "retrieve_context", side_effect=_retrieve_context)
            )
            stack.enter_context(
                patch.object(routes_chat, "_stream_normal", side_effect=_stream_answer)
            )
            stack.enter_context(patch.object(routes_chat, "record_event"))
            response = await routes_chat.chat(payload, db_session)
            return await _consume_response(response)

    stream = asyncio.run(run_without_redis())

    assert "event: done" in stream
    assert db_session.execute(
        select(ChatHistory).where(ChatHistory.content == "无 Redis 问题")
    ).scalars().one().role == "user"


@pytest.mark.parametrize("status", ["processing", "completed", "failed"])
def test_turn_status_endpoint_returns_only_owned_status(db_session, status):
    turn_id = uuid.uuid4()
    turn = ChatTurn(
        client_turn_id=str(turn_id),
        user_id="u1",
        session_id="s1",
        request_fingerprint="a" * 64,
        status=status,
    )
    if status == "processing":
        turn.lease_owner = str(uuid.uuid4())
        turn.lease_expires_at = datetime.utcnow() + timedelta(minutes=1)
    db_session.add(turn)
    db_session.commit()

    result = routes_chat.get_chat_turn_status(
        user_id="u1",
        session_id="s1",
        client_turn_id=turn_id,
        db=db_session,
    )

    assert result.model_dump(mode="json") == {
        "client_turn_id": str(turn_id),
        "status": status,
    }


@pytest.mark.parametrize(
    ("lease_owner", "lease_expires_at"),
    [
        (None, None),
        ("expired-owner", datetime.utcnow() - timedelta(minutes=1)),
    ],
)
def test_turn_status_reports_expired_processing_lease_as_failed(
    db_session,
    lease_owner,
    lease_expires_at,
):
    """状态查询不改库，但必须让客户端知道该 turn 已可安全重试。"""
    turn_id = uuid.uuid4()
    db_session.add(ChatTurn(
        client_turn_id=str(turn_id),
        user_id="u1",
        session_id="s1",
        request_fingerprint="a" * 64,
        status="processing",
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    ))
    db_session.commit()

    result = routes_chat.get_chat_turn_status(
        user_id="u1",
        session_id="s1",
        client_turn_id=turn_id,
        db=db_session,
    )

    assert result.status == "failed"


def test_turn_status_endpoint_hides_turn_from_other_session(db_session):
    turn_id = uuid.uuid4()
    db_session.add(SessionModel(id="s2", user_id="u1"))
    db_session.add(ChatTurn(
        client_turn_id=str(turn_id),
        user_id="u1",
        session_id="s1",
        request_fingerprint="a" * 64,
        status="processing",
    ))
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        routes_chat.get_chat_turn_status(
            user_id="u1",
            session_id="s2",
            client_turn_id=turn_id,
            db=db_session,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "请求不存在"


def test_completion_session_query_uses_mysql_for_update_lock():
    payload = ChatRequest(user_id="u1", session_id="s1", message="并发问题")

    statement = routes_chat._select_session_for_completion(payload)
    sql = str(statement.compile(
        dialect=mysql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "FROM sessions" in sql
    assert "sessions.id = 's1'" in sql
    assert "sessions.user_id = 'u1'" in sql
    assert sql.rstrip().endswith("FOR UPDATE")


def test_completed_turn_links_one_ordered_user_assistant_pair(db_session):
    turn_id = uuid.uuid4()

    stream = asyncio.run(_collect_chat(
        db_session,
        FakeRedis(),
        message="成对写入问题",
        client_turn_id=turn_id,
    ))

    turn = db_session.get(ChatTurn, str(turn_id))
    user_message = db_session.get(ChatHistory, turn.user_message_id)
    assistant_message = db_session.get(ChatHistory, turn.assistant_message_id)
    status = routes_chat.get_chat_turn_status(
        user_id="u1",
        session_id="s1",
        client_turn_id=turn_id,
        db=db_session,
    )
    history = routes_chat.get_chat_history(user_id="u1", session_id="s1", db=db_session)
    assert "event: done" in stream
    assert status.status == "completed"
    assert (user_message.role, assistant_message.role) == ("user", "assistant")
    assert user_message.id < assistant_message.id
    assert user_message.session_id == assistant_message.session_id == "s1"
    assert [(item.role, item.content) for item in history[-2:]] == [
        ("user", "成对写入问题"),
        ("assistant", "本次回答"),
    ]


def test_stream_deep_exposes_agent_b_retrieved_documents_as_sources(monkeypatch):
    """Agent B 的检索结果应转成 deep 模式的结构化来源事件。"""
    class FakeGraph:
        async def astream(self, state, stream_mode):
            yield "updates", {
                "agent_b": {
                    "retrieved_docs": [
                        {
                            "content": "文档片段",
                            "source": "deep-guide.pdf",
                            "sub_question": "子问题",
                            "score": 0.82,
                        }
                    ]
                }
            }
            yield "custom", {"type": "token", "content": "深度回答"}

    monkeypatch.setattr("agents.graph.get_graph", lambda: FakeGraph())
    payload = ChatRequest(user_id="u1", session_id="s1", message="深入分析", mode="deep")

    async def collect_events():
        return [event async for event in routes_chat._stream_deep(payload, {})]

    events = asyncio.run(collect_events())

    assert {"type": "sources", "content": [
        {"source": "deep-guide.pdf", "score": 0.82},
    ]} in events


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
    assert get_messages(redis, "u1", "s1") == []
    session = db_session.get(SessionModel, "s1")
    assert session.title is None
    assert "event: error" in stream


def test_empty_answer_is_failed_and_never_persisted(db_session):
    """模型无异常但没有正文时不能留下空 assistant 或 completed turn。"""
    turn_id = uuid.uuid4()

    stream = asyncio.run(_collect_chat(
        db_session,
        FakeRedis(),
        stream_factory=_stream_empty,
        client_turn_id=turn_id,
    ))

    rows = db_session.execute(
        select(ChatHistory).order_by(ChatHistory.id.asc())
    ).scalars().all()
    turn = db_session.get(ChatTurn, str(turn_id))

    assert [(row.role, row.content) for row in rows] == [
        ("user", "历史问题"),
        ("assistant", "历史回答"),
    ]
    assert turn.status == "failed"
    assert "event: error" in stream
    assert "event: done" not in stream


def test_retrieval_timeout_marks_turn_failed_without_half_history(db_session, monkeypatch):
    """检索阶段超过边界时停止续租，且不写入当前问答事实。"""
    turn_id = uuid.uuid4()

    async def hanging_retrieval(**kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(routes_chat, "CHAT_STAGE_TIMEOUT_SECONDS", 0.01)
    stream = asyncio.run(_collect_chat(
        db_session,
        FakeRedis(),
        client_turn_id=turn_id,
        retrieve_factory=hanging_retrieval,
    ))

    rows = db_session.execute(
        select(ChatHistory).order_by(ChatHistory.id.asc())
    ).scalars().all()
    turn = db_session.get(ChatTurn, str(turn_id))

    assert [(row.role, row.content) for row in rows] == [
        ("user", "历史问题"),
        ("assistant", "历史回答"),
    ]
    assert turn.status == "failed"
    assert "请求超时，请重新提问" in stream
    assert "event: done" not in stream


def test_chat_turn_logs_stage_timings_without_user_content(db_session, caplog):
    """一次 turn 可串联检索、首 token 和完成事件，且不泄露用户正文。"""
    secret_question = "这是不可进入日志的完整私密问题"
    caplog.set_level(logging.INFO)

    stream = asyncio.run(
        _collect_chat(db_session, FakeRedis(), message=secret_question)
    )

    events = []
    for record in caplog.records:
        try:
            event = json.loads(record.getMessage())
        except (json.JSONDecodeError, TypeError):
            continue
        if event.get("event", "").startswith("turn_"):
            events.append(event)

    assert "event: done" in stream
    assert [event["event"] for event in events] == [
        "turn_started",
        "turn_retrieval_completed",
        "turn_first_token",
        "turn_completed",
    ]
    assert len({event["turn_id"] for event in events}) == 1
    assert len({event["request_id"] for event in events}) == 1
    assert secret_question not in caplog.text
