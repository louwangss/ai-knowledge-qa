"""MySQL 权威会话记忆的回归测试。"""

from unittest.mock import patch

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import ChatHistory, Session as SessionModel, SessionSummary, User
from memory.conversation import load_conversation_memory, maybe_compress_conversation


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_sqlite_integer(type_, compiler, **kwargs):
    return "INTEGER"


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
    db.add(User(id="u1", username="test-user"))
    db.add(SessionModel(id="s1", user_id="u1"))
    db.commit()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _add_rounds(db, count: int):
    for index in range(count):
        db.add(ChatHistory(
            user_id="u1",
            session_id="s1",
            role="user",
            content=f"问题 {index + 1}",
        ))
        db.add(ChatHistory(
            user_id="u1",
            session_id="s1",
            role="assistant",
            content=f"回答 {index + 1}",
        ))
    db.commit()


def test_load_conversation_memory_uses_mysql_summary_and_uncompressed_messages(db_session):
    _add_rounds(db_session, 6)
    db_session.add(SessionSummary(
        session_id="s1",
        summary="第一轮摘要",
        compressed_count=10,
    ))
    db_session.commit()

    memory = load_conversation_memory(db_session, "u1", "s1")

    assert memory["summary"] == "第一轮摘要"
    assert [(item["role"], item["content"]) for item in memory["messages"]] == [
        ("user", "问题 6"),
        ("assistant", "回答 6"),
    ]


def test_load_conversation_memory_does_not_expose_another_users_summary(db_session):
    db_session.add(User(id="u2", username="other-user"))
    db_session.add(SessionModel(id="s2", user_id="u2"))
    db_session.add(SessionSummary(
        session_id="s2",
        summary="其他用户的私密摘要",
        compressed_count=0,
    ))
    db_session.commit()

    memory = load_conversation_memory(db_session, "u1", "s2")

    assert memory == {"summary": None, "messages": []}


@pytest.mark.parametrize("compressed_count", [2, 3, 999])
def test_invalid_compressed_cursor_falls_back_to_complete_authoritative_history(
    db_session,
    compressed_count,
):
    _add_rounds(db_session, 2)
    db_session.add(SessionSummary(
        session_id="s1",
        summary="损坏的派生摘要",
        compressed_count=compressed_count,
    ))
    db_session.commit()

    memory = load_conversation_memory(db_session, "u1", "s1")

    assert memory["summary"] is None
    assert [item["content"] for item in memory["messages"]] == [
        "问题 1", "回答 1", "问题 2", "回答 2",
    ]


def test_compression_waits_until_existing_first_trigger(db_session):
    _add_rounds(db_session, 10)

    with patch("memory.conversation._generate_summary") as generate:
        assert maybe_compress_conversation(db_session, "u1", "s1") is False

    generate.assert_not_called()
    assert db_session.query(SessionSummary).count() == 0


def test_first_compression_summarizes_oldest_five_rounds(db_session):
    _add_rounds(db_session, 11)

    with patch("memory.conversation._generate_summary", return_value="持久化摘要") as generate:
        assert maybe_compress_conversation(db_session, "u1", "s1") is True

    existing_summary, messages = generate.call_args.args
    assert existing_summary == ""
    assert [item["content"] for item in messages] == [
        "问题 1", "回答 1", "问题 2", "回答 2", "问题 3",
        "回答 3", "问题 4", "回答 4", "问题 5", "回答 5",
    ]
    record = db_session.query(SessionSummary).one()
    assert record.summary == "持久化摘要"
    assert record.compressed_count == 10

    loaded = load_conversation_memory(db_session, "u1", "s1")
    assert loaded["messages"][0]["content"] == "问题 6"


def test_follow_up_compression_uses_persisted_progress(db_session):
    _add_rounds(db_session, 16)
    db_session.add(SessionSummary(
        session_id="s1",
        summary="已有摘要",
        compressed_count=10,
    ))
    db_session.commit()

    with patch("memory.conversation._generate_summary", return_value="更新摘要") as generate:
        assert maybe_compress_conversation(db_session, "u1", "s1") is True

    existing_summary, messages = generate.call_args.args
    assert existing_summary == "已有摘要"
    assert messages[0]["content"] == "问题 6"
    assert messages[-1]["content"] == "回答 10"
    record = db_session.query(SessionSummary).one()
    assert record.summary == "更新摘要"
    assert record.compressed_count == 20


def test_follow_up_compression_waits_through_round_fifteen(db_session):
    _add_rounds(db_session, 15)
    db_session.add(SessionSummary(
        session_id="s1",
        summary="已有摘要",
        compressed_count=10,
    ))
    db_session.commit()

    with patch("memory.conversation._generate_summary") as generate:
        assert maybe_compress_conversation(db_session, "u1", "s1") is False

    generate.assert_not_called()


def test_failed_summary_generation_keeps_existing_progress(db_session):
    _add_rounds(db_session, 11)

    with patch("memory.conversation._generate_summary", return_value=""):
        assert maybe_compress_conversation(db_session, "u1", "s1") is False

    assert db_session.query(SessionSummary).count() == 0


def test_failed_or_missing_summary_still_bounds_prompt_history(db_session):
    _add_rounds(db_session, 20)

    memory = load_conversation_memory(db_session, "u1", "s1")

    assert len(memory["messages"]) == 20
    assert memory["messages"][0]["content"] == "问题 11"
    assert memory["messages"][-1]["content"] == "回答 20"


def test_late_compression_cannot_overwrite_newer_summary(db_session):
    _add_rounds(db_session, 16)
    db_session.add(SessionSummary(
        session_id="s1",
        summary="旧摘要",
        compressed_count=10,
    ))
    db_session.commit()

    def concurrent_update(existing_summary, messages):
        record = db_session.query(SessionSummary).one()
        record.summary = "并发产生的新摘要"
        record.compressed_count = 20
        db_session.commit()
        return "迟到的摘要"

    with patch("memory.conversation._generate_summary", side_effect=concurrent_update):
        assert maybe_compress_conversation(db_session, "u1", "s1") is False

    record = db_session.query(SessionSummary).one()
    assert record.summary == "并发产生的新摘要"
    assert record.compressed_count == 20


def test_first_compression_loser_keeps_concurrent_insert(db_session):
    _add_rounds(db_session, 11)

    def concurrent_insert(existing_summary, messages):
        db_session.add(SessionSummary(
            session_id="s1",
            summary="并发先写入的摘要",
            compressed_count=10,
        ))
        db_session.commit()
        return "迟到的摘要"

    with patch("memory.conversation._generate_summary", side_effect=concurrent_insert):
        assert maybe_compress_conversation(db_session, "u1", "s1") is False

    record = db_session.query(SessionSummary).one()
    assert record.summary == "并发先写入的摘要"
    assert record.compressed_count == 10
