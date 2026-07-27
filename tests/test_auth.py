"""单用户 Bearer token 与资源归属边界测试。"""

import os
import subprocess
import sys
from pathlib import Path

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

AUTH_HEADERS = {"Authorization": "Bearer test-access-token"}
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    return db


@pytest.fixture
def client(mock_db):
    from app.deps import get_db
    from app.main import app

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_root_is_public(client):
    response = client.get("/")

    assert response.status_code == 200


def test_protected_route_rejects_missing_token(client):
    response = client.get("/api/v1/sessions", params={"user_id": "u1"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_protected_route_rejects_wrong_token_without_leaking_it(client, caplog):
    wrong_token = "wrong-token-must-not-leak"

    response = client.get(
        "/api/v1/sessions",
        params={"user_id": "u1"},
        headers={"Authorization": f"Bearer {wrong_token}"},
    )

    assert response.status_code == 401
    assert wrong_token not in response.text
    assert wrong_token not in caplog.text


def test_correct_token_can_access_owned_resource(client):
    response = client.get(
        "/api/v1/sessions",
        params={"user_id": "u1"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs"),
    [
        ("POST", "/api/v1/sessions", {"json": {"user_id": "other-user"}}),
        ("GET", "/api/v1/sessions", {"params": {"user_id": "other-user"}}),
        ("DELETE", "/api/v1/sessions/s1", {"params": {"user_id": "other-user"}}),
        (
            "POST",
            "/api/v1/documents",
            {
                "data": {"user_id": "other-user"},
                "files": {"file": ("note.txt", b"content", "text/plain")},
            },
        ),
        ("GET", "/api/v1/documents", {"params": {"user_id": "other-user"}}),
        ("DELETE", "/api/v1/documents/d1", {"params": {"user_id": "other-user"}}),
        (
            "POST",
            "/api/v1/notes",
            {"json": {"user_id": "other-user", "concept": "c", "content": "n"}},
        ),
        ("GET", "/api/v1/notes", {"params": {"user_id": "other-user"}}),
        (
            "PUT",
            "/api/v1/notes/1",
            {"params": {"user_id": "other-user"}, "json": {"content": "changed"}},
        ),
        ("DELETE", "/api/v1/notes/1", {"params": {"user_id": "other-user"}}),
        (
            "POST",
            "/api/v1/chat",
            {
                "json": {
                    "user_id": "other-user",
                    "session_id": "s1",
                    "message": "越权问题",
                    "mode": "normal",
                }
            },
        ),
        (
            "GET",
            "/api/v1/chat/history",
            {"params": {"user_id": "other-user", "session_id": "s1"}},
        ),
    ],
)
def test_other_user_id_is_rejected_before_resource_access(
    client,
    method,
    path,
    request_kwargs,
):
    response = client.request(
        method,
        path,
        headers=AUTH_HEADERS,
        **request_kwargs,
    )

    assert response.status_code == 403


def test_user_endpoint_returns_fixed_app_user_id(mock_db):
    from app.api.routes_users import create_user
    from app.models.schemas import UserCreate

    user = create_user(UserCreate(username="user"), db=mock_db)

    assert user.id == "u1"


def test_openapi_does_not_contain_access_token(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "test-access-token" not in response.text
    schema = response.json()
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for operation in operations.values():
            assert operation["security"] == [{"AppBearerAuth": []}]


def test_non_ascii_credentials_and_user_id_are_rejected_normally():
    from app.deps import require_access_token, require_app_user

    with pytest.raises(HTTPException) as token_error:
        require_access_token(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="错误令牌")
        )
    with pytest.raises(HTTPException) as user_error:
        require_app_user("其他用户")

    assert token_error.value.status_code == 401
    assert user_error.value.status_code == 403


def test_wrong_token_cannot_open_sse_stream_or_leak_token(client):
    wrong_token = "wrong-sse-token-must-not-leak"

    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {wrong_token}"},
        json={
            "user_id": "u1",
            "session_id": "s1",
            "message": "hello",
            "mode": "normal",
        },
    )

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert wrong_token not in response.text


def test_config_rejects_app_user_id_longer_than_database_column():
    env = os.environ.copy()
    env["APP_USER_ID"] = "x" * 37
    env["PYTHONUTF8"] = "1"

    completed = subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert "APP_USER_ID 长度不能超过 36" in completed.stderr
