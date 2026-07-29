"""数据库 additive upgrade 与启动前 schema 门禁回归测试。"""

import pytest
from sqlalchemy import BigInteger, create_engine, inspect, select, text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import ChatHistory, ChatTurn, SchemaVersion, Session as SessionModel, User
from db.schema import (
    CURRENT_SCHEMA_VERSION,
    SchemaNotReadyError,
    assert_schema_ready,
    upgrade_schema,
)


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_sqlite_integer(type_, compiler, **kwargs):
    return "INTEGER"


@pytest.fixture
def legacy_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    legacy_tables = [
        table for table in Base.metadata.sorted_tables
        if table.name not in {"chat_sources", "chat_turns", "schema_version"}
    ]
    Base.metadata.create_all(engine, tables=legacy_tables)
    with Session(engine) as db:
        db.add(User(id="u1", username="existing-user"))
        db.add(SessionModel(id="s1", user_id="u1"))
        db.add(ChatHistory(
            id=1,
            user_id="u1",
            session_id="s1",
            role="user",
            content="升级前数据",
        ))
        db.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def test_schema_gate_rejects_legacy_database_with_upgrade_instruction(legacy_engine):
    with pytest.raises(SchemaNotReadyError, match=r"python -m db\.init_db --upgrade"):
        assert_schema_ready(legacy_engine)


def test_additive_upgrade_is_idempotent_and_preserves_existing_data(legacy_engine):
    upgrade_schema(legacy_engine)
    upgrade_schema(legacy_engine)

    inspector = inspect(legacy_engine)
    assert "chat_sources" in inspector.get_table_names()
    assert "chat_turns" in inspector.get_table_names()
    assert "schema_version" in inspector.get_table_names()
    assert "idx_chat_source_message" in {
        item["name"] for item in inspector.get_indexes("chat_sources")
    }
    assert {
        "idx_chat_turn_session",
        "idx_chat_turn_status",
    } <= {
        item["name"] for item in inspector.get_indexes("chat_turns")
    }
    assert_schema_ready(legacy_engine)

    with Session(legacy_engine) as db:
        assert db.scalar(select(SchemaVersion.version).where(SchemaVersion.id == 1)) == CURRENT_SCHEMA_VERSION
        assert db.scalar(select(ChatHistory.content).where(ChatHistory.id == 1)) == "升级前数据"


def test_schema_version_two_adds_chat_turns_without_altering_history():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    version_one_tables = [
        table for table in Base.metadata.sorted_tables
        if table.name != "chat_turns"
    ]
    Base.metadata.create_all(engine, tables=version_one_tables)
    with Session(engine) as db:
        db.add(SchemaVersion(id=1, version=1))
        db.add(User(id="u1", username="existing-user"))
        db.add(SessionModel(id="s1", user_id="u1"))
        db.add(ChatHistory(
            id=1,
            user_id="u1",
            session_id="s1",
            role="user",
            content="v1 数据",
        ))
        db.commit()

    try:
        upgrade_schema(engine)

        inspector = inspect(engine)
        assert "chat_turns" in inspector.get_table_names()
        with Session(engine) as db:
            assert db.get(SchemaVersion, 1).version == CURRENT_SCHEMA_VERSION
            assert db.get(ChatHistory, 1).content == "v1 数据"
    finally:
        engine.dispose()


def test_schema_version_two_adds_turn_lease_columns_and_preserves_rows():
    """create_all 不会补列，v2→v3 必须显式 ALTER 且可重复执行。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    version_two_tables = [
        table for table in Base.metadata.sorted_tables
        if table.name != "chat_turns"
    ]
    Base.metadata.create_all(engine, tables=version_two_tables)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE chat_turns (
                client_turn_id VARCHAR(36) NOT NULL PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                session_id VARCHAR(36) NOT NULL,
                request_fingerprint VARCHAR(64) NOT NULL,
                status VARCHAR(20) NOT NULL,
                user_message_id BIGINT NULL,
                assistant_message_id BIGINT NULL,
                created_at TIMESTAMP NULL,
                updated_at TIMESTAMP NULL,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (user_message_id) REFERENCES chat_history(id) ON DELETE SET NULL,
                FOREIGN KEY (assistant_message_id) REFERENCES chat_history(id) ON DELETE SET NULL
            )
        """))

    with Session(engine) as db:
        db.add(SchemaVersion(id=1, version=2))
        db.add(User(id="u1", username="existing-user"))
        db.add(SessionModel(id="s1", user_id="u1"))
        db.commit()
        db.execute(text("""
            INSERT INTO chat_turns (
                client_turn_id, user_id, session_id, request_fingerprint, status
            ) VALUES (
                '00000000-0000-4000-8000-000000000001',
                'u1', 's1', :fingerprint, 'processing'
            )
        """), {"fingerprint": "a" * 64})
        db.commit()

    try:
        upgrade_schema(engine)
        upgrade_schema(engine)

        inspector = inspect(engine)
        columns = {item["name"] for item in inspector.get_columns("chat_turns")}
        assert {"lease_owner", "lease_expires_at"} <= columns
        indexes = {item["name"] for item in inspector.get_indexes("chat_turns")}
        assert "idx_chat_turn_lease" in indexes
        assert_schema_ready(engine)

        with Session(engine) as db:
            turn = db.get(ChatTurn, "00000000-0000-4000-8000-000000000001")
            assert turn.status == "processing"
            assert turn.lease_owner is None
            assert turn.lease_expires_at is None
            assert db.get(SchemaVersion, 1).version == CURRENT_SCHEMA_VERSION
    finally:
        engine.dispose()


def test_upgrade_rejects_chat_turn_table_without_authoritative_foreign_keys():
    """租约状态必须受用户、会话与消息外键约束，不能接受可产生孤儿 turn 的结构。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [table for table in Base.metadata.sorted_tables if table.name != "chat_turns"]
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE chat_turns (
                client_turn_id VARCHAR(36) NOT NULL PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                session_id VARCHAR(36) NOT NULL,
                request_fingerprint VARCHAR(64) NOT NULL,
                status VARCHAR(20) NOT NULL,
                user_message_id BIGINT NULL,
                assistant_message_id BIGINT NULL,
                lease_owner VARCHAR(36) NULL,
                lease_expires_at DATETIME NULL,
                created_at TIMESTAMP NULL,
                updated_at TIMESTAMP NULL
            )
        """))

    try:
        with pytest.raises(SchemaNotReadyError, match="缺少必需外键"):
            upgrade_schema(engine)
    finally:
        engine.dispose()
