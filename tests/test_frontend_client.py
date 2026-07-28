"""Gradio 前端 API 认证头与本机监听配置测试。"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest


def test_all_frontend_requests_include_bearer_token(tmp_path, monkeypatch):
    from frontend import app as frontend_app

    captured_requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        path = request.url.path
        method = request.method

        if path == "/api/v1/users":
            return httpx.Response(200, json={"id": "u1"})
        if path == "/api/v1/sessions" and method == "POST":
            return httpx.Response(200, json={"id": "s1"})
        if path == "/api/v1/sessions":
            return httpx.Response(200, json=[])
        if path == "/api/v1/chat/history":
            return httpx.Response(200, json=[])
        if path == "/api/v1/documents" and method == "POST":
            return httpx.Response(
                200,
                json={"filename": "note.txt", "chunk_count": 1},
            )
        if path == "/api/v1/documents":
            return httpx.Response(200, json=[])
        if path == "/api/v1/notes" and method == "GET":
            return httpx.Response(200, json=[])
        if path == "/api/v1/notes" and method == "POST":
            return httpx.Response(200, json={"id": 1})
        if path.startswith("/api/v1/notes/") and method == "PUT":
            return httpx.Response(200, json={"id": 1})
        if path == "/api/v1/chat":
            return httpx.Response(
                200,
                content=(
                    'event: token\ndata: {"content":"回答"}\n\n'
                    "event: done\ndata: {}\n\n"
                ).encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={"detail": "删除成功"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(frontend_app.httpx, "AsyncClient", client_factory)
    upload_path = tmp_path / "note.txt"
    upload_path.write_text("content", encoding="utf-8")

    async def exercise_all_requests():
        await frontend_app.api_create_user()
        await frontend_app.api_create_session("u1")
        await frontend_app.api_list_sessions("u1")
        await frontend_app.api_delete_session("u1", "s1")
        await frontend_app.api_get_chat_history("u1", "s1")
        await frontend_app.api_upload_document(
            "u1",
            SimpleNamespace(name=str(upload_path)),
        )
        await frontend_app.api_list_documents("u1")
        await frontend_app.api_delete_document("u1", "d1")
        await frontend_app.api_list_notes("u1")
        await frontend_app.api_save_note("u1", "标题", "内容", None)
        await frontend_app.api_save_note("u1", "标题", "更新", 1)
        await frontend_app.api_delete_note("u1", 1)
        await frontend_app.api_get_documents_dropdown("u1")
        async for _ in frontend_app.chat_fn(
            "问题",
            [],
            "normal",
            "u1",
            "s1",
        ):
            pass

    asyncio.run(exercise_all_requests())

    assert len(captured_requests) == 14
    assert {
        request.headers.get("authorization") for request in captured_requests
    } == {"Bearer test-access-token"}


def test_frontend_initialization_error_does_not_expose_token(monkeypatch):
    from frontend import app as frontend_app

    secret = "test-access-token"

    async def fail_create_user():
        raise RuntimeError(f"认证失败: {secret}")

    monkeypatch.setattr(frontend_app, "api_create_user", fail_create_user)

    result = asyncio.run(frontend_app.init_app())

    assert secret not in result[-1]


def test_frontend_initialization_loads_first_screen_data_in_one_stage(monkeypatch):
    from frontend import app as frontend_app

    started = set()
    all_started = asyncio.Event()
    first_stage = {"user", "documents", "sessions", "notes"}

    async def finish_first_stage(name, value):
        started.add(name)
        if started == first_stage:
            all_started.set()
        await all_started.wait()
        return value

    async def fake_create_user():
        return await finish_first_stage("user", "u1")

    async def fake_fetch_documents(user_id):
        assert user_id == "u1"
        return await finish_first_stage(
            "documents",
            [{"id": "document-1", "filename": "知识.md", "chunk_count": 2,
              "created_at": "2026-07-28T00:00:00"}],
        )

    async def fake_list_sessions(user_id):
        assert user_id == "u1"
        return await finish_first_stage(
            "sessions",
            [{"id": "session-1", "title": "已有会话"}],
        )

    async def fake_list_notes(user_id):
        assert user_id == "u1"
        return await finish_first_stage("notes", [("笔记", 1)])

    async def fake_history(user_id, session_id):
        assert (user_id, session_id) == ("u1", "session-1")
        return [{"role": "user", "content": "历史问题"}]

    monkeypatch.setattr(frontend_app, "APP_USER_ID", "u1", raising=False)
    monkeypatch.setattr(frontend_app, "api_create_user", fake_create_user)
    monkeypatch.setattr(
        frontend_app,
        "_api_fetch_documents",
        fake_fetch_documents,
        raising=False,
    )
    monkeypatch.setattr(frontend_app, "api_list_sessions", fake_list_sessions)
    monkeypatch.setattr(frontend_app, "api_list_notes", fake_list_notes)
    monkeypatch.setattr(frontend_app, "api_get_chat_history", fake_history)

    result = asyncio.run(asyncio.wait_for(frontend_app.init_app(), timeout=1.0))

    assert started == first_stage
    assert result[0] == "u1"
    assert result[1] == "session-1"
    assert result[2] == [["知识.md", 2, "2026-07-28T00:00:00"]]
    assert result[3]["choices"] == [("知识.md (#document)", "document-1")]
    assert result[5] == [{"role": "user", "content": "历史问题"}]
    assert result[6]["choices"] == [("笔记", 1)]
    assert result[7] == [("笔记", 1)]
    assert result[8] == "就绪"


def test_default_bind_hosts_are_loopback():
    from app import main as api_main
    from frontend import app as frontend_app

    assert api_main.API_HOST == "127.0.0.1"
    assert frontend_app.GRADIO_HOST == "127.0.0.1"


def test_server_launchers_use_configured_hosts(monkeypatch):
    from app import main as api_main
    from frontend import app as frontend_app
    import uvicorn

    api_call = {}
    gradio_call = {}

    def fake_uvicorn_run(*args, **kwargs):
        api_call.update({"args": args, "kwargs": kwargs})

    class FakeUI:
        def launch(self, **kwargs):
            gradio_call.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr(api_main, "API_HOST", "127.0.0.2", raising=False)
    monkeypatch.setattr(frontend_app, "GRADIO_HOST", "127.0.0.3", raising=False)
    monkeypatch.setattr(frontend_app, "build_ui", lambda: FakeUI())

    api_main.run_api()
    frontend_app.run_frontend()

    assert api_call["kwargs"]["host"] == "127.0.0.2"
    assert gradio_call["server_name"] == "127.0.0.3"


def test_external_frontend_bind_requires_access_token():
    from frontend import app as frontend_app

    with pytest.raises(RuntimeError, match="APP_ACCESS_TOKEN"):
        frontend_app.validate_frontend_config(
            access_token="",
            server_name="0.0.0.0",
        )
