"""ChatTurn 启动恢复回归测试。"""

from datetime import datetime, timedelta

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.startup import mark_interrupted_chat_turns_failed
from db.database import Base
from db.models import ChatTurn, Session as SessionModel, User


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_sqlite_integer(type_, compiler, **kwargs):
    return "INTEGER"


def test_startup_only_marks_expired_or_unleased_processing_turns_failed():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(User(id="u1", username="test-user"))
            db.add(SessionModel(id="s1", user_id="u1"))
            db.add_all([
                ChatTurn(
                    client_turn_id="00000000-0000-4000-8000-000000000001",
                    user_id="u1",
                    session_id="s1",
                    request_fingerprint="a" * 64,
                    status="processing",
                    lease_owner="active-owner",
                    lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
                ),
                ChatTurn(
                    client_turn_id="00000000-0000-4000-8000-000000000002",
                    user_id="u1",
                    session_id="s1",
                    request_fingerprint="b" * 64,
                    status="processing",
                    lease_owner="expired-owner",
                    lease_expires_at=datetime.utcnow() - timedelta(minutes=5),
                ),
                ChatTurn(
                    client_turn_id="00000000-0000-4000-8000-000000000003",
                    user_id="u1",
                    session_id="s1",
                    request_fingerprint="c" * 64,
                    status="processing",
                ),
                ChatTurn(
                    client_turn_id="00000000-0000-4000-8000-000000000004",
                    user_id="u1",
                    session_id="s1",
                    request_fingerprint="d" * 64,
                    status="completed",
                ),
            ])
            db.commit()

            assert mark_interrupted_chat_turns_failed(db) == 2

            turns = {
                item.client_turn_id: item
                for item in db.execute(select(ChatTurn)).scalars()
            }
            states = {turn_id: item.status for turn_id, item in turns.items()}
            assert states == {
                "00000000-0000-4000-8000-000000000001": "processing",
                "00000000-0000-4000-8000-000000000002": "failed",
                "00000000-0000-4000-8000-000000000003": "failed",
                "00000000-0000-4000-8000-000000000004": "completed",
            }
            assert turns["00000000-0000-4000-8000-000000000001"].lease_owner == "active-owner"
            assert turns["00000000-0000-4000-8000-000000000002"].lease_owner is None
            assert turns["00000000-0000-4000-8000-000000000002"].lease_expires_at is None
    finally:
        engine.dispose()
