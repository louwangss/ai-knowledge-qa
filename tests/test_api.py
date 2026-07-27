"""API 端点测试：root + sessions + documents

使用 TestClient + dependency_overrides mock 数据库。
mock_db 作为独立 fixture 暴露，测试函数可直接配置其返回值。
"""
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

AUTH_HEADERS = {"Authorization": "Bearer test-access-token"}


@pytest.fixture
def mock_db():
    """共享的 mock 数据库 Session"""
    return MagicMock()


@pytest.fixture
def client(mock_db):
    """TestClient，DB 依赖被 mock_db 替换"""
    from app.main import app
    from app.deps import get_db

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_root(client):
    """根路径返回状态"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_list_sessions_empty(client, mock_db):
    """空用户的会话列表"""
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    resp = client.get(
        "/api/v1/sessions",
        params={"user_id": "u1"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []



def test_documents_list_empty(client, mock_db):
    """空文档列表"""
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    resp = client.get(
        "/api/v1/documents",
        params={"user_id": "u1"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []
