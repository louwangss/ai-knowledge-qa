"""Gradio 首屏聚合读取接口测试。"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import ChatHistory, Document, SemanticMemory, Session as SessionModel, User


AUTH_HEADERS = {"Authorization": "Bearer test-access-token"}


def test_bootstrap_returns_latest_session_snapshot_in_one_response():
    from app.deps import get_db
    from app.main import app

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    now = datetime(2026, 7, 28, 12, 0, 0)
    try:
        db.add(User(id="u1", username="owner"))
        db.add_all([
            SessionModel(
                id="older-session",
                user_id="u1",
                title="旧会话",
                last_active=now - timedelta(hours=1),
            ),
            SessionModel(
                id="latest-session",
                user_id="u1",
                title="最近会话",
                last_active=now,
            ),
            Document(
                id="ready-document",
                user_id="u1",
                filename="知识.md",
                file_type=".md",
                file_path="unused",
                chunk_count=2,
                chunk_size=1000,
                chunk_overlap=200,
                content_hash="ready-hash",
                status="ready",
                created_at=now,
            ),
            Document(
                id="processing-document",
                user_id="u1",
                filename="处理中.md",
                file_type=".md",
                file_path="unused",
                chunk_size=1000,
                chunk_overlap=200,
                content_hash="processing-hash",
                status="processing",
                created_at=now,
            ),
            SemanticMemory(
                id=1,
                user_id="u1",
                concept="笔记标题",
                content="笔记内容",
                created_at=now,
                updated_at=now,
            ),
        ])
        db.flush()
        db.add_all([
            ChatHistory(
                id=1,
                user_id="u1",
                session_id="latest-session",
                role="user",
                content="问题",
                created_at=now,
            ),
            ChatHistory(
                id=2,
                user_id="u1",
                session_id="latest-session",
                role="assistant",
                content="回答",
                created_at=now + timedelta(seconds=1),
            ),
            ChatHistory(
                id=3,
                user_id="u1",
                session_id="older-session",
                role="user",
                content="不应返回",
                created_at=now,
            ),
        ])
        db.commit()

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        response = TestClient(app).get(
            "/api/v1/bootstrap",
            params={"user_id": "u1"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        payload = response.json()
        assert [item["id"] for item in payload["sessions"]] == [
            "latest-session",
            "older-session",
        ]
        assert [item["id"] for item in payload["documents"]] == ["ready-document"]
        assert payload["notes"] == [{"id": 1, "concept": "笔记标题"}]
        assert payload["active_note"] == {
            "id": 1,
            "concept": "笔记标题",
            "content": "笔记内容",
        }
        assert payload["history"] == [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
    finally:
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
