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
        if path == "/api/v1/bootstrap":
            return httpx.Response(
                200,
                json={
                    "documents": [], "sessions": [], "notes": [], "history": [],
                    "active_note": None,
                },
            )
        if path == "/api/v1/documents" and method == "POST":
            return httpx.Response(
                200,
                json={"filename": "note.txt", "chunk_count": 1},
            )
        if path == "/api/v1/documents":
            return httpx.Response(200, json=[])
        if path == "/api/v1/notes/summaries" and method == "GET":
            return httpx.Response(200, json=[])
        if path == "/api/v1/notes/1" and method == "GET":
            return httpx.Response(
                200,
                json={"id": 1, "concept": "标题", "content": "内容"},
            )
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
        await frontend_app.api_get_note("u1", 1)
        await frontend_app.api_save_note("u1", "标题", "内容", None)
        await frontend_app.api_save_note("u1", "标题", "更新", 1)
        await frontend_app.api_delete_note("u1", 1)
        await frontend_app.api_get_documents_dropdown("u1")
        await frontend_app.api_get_bootstrap("u1")
        async for _ in frontend_app.chat_fn(
            "问题",
            [],
            "normal",
            "u1",
            "s1",
        ):
            pass

    asyncio.run(exercise_all_requests())

    assert len(captured_requests) == 16
    assert {
        request.headers.get("authorization") for request in captured_requests
    } == {"Bearer test-access-token"}
    assert any(request.url.path == "/api/v1/notes/summaries" for request in captured_requests)


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
    first_stage = {"user", "bootstrap"}

    async def finish_first_stage(name, value):
        started.add(name)
        if started == first_stage:
            all_started.set()
        await all_started.wait()
        return value

    async def fake_create_user():
        return await finish_first_stage("user", "u1")

    async def fake_get_bootstrap(user_id):
        assert user_id == "u1"
        return await finish_first_stage(
            "bootstrap",
            {
                "documents": [
                    {"id": "document-1", "filename": "知识.md", "chunk_count": 2,
                     "created_at": "2026-07-28T00:00:00"},
                ],
                "sessions": [{"id": "session-1", "title": "已有会话"}],
                "notes": [{"id": 1, "concept": "笔记"}],
                "active_note": {"id": 1, "concept": "笔记", "content": "笔记正文"},
                "history": [{"role": "user", "content": "历史问题"}],
            },
        )

    monkeypatch.setattr(frontend_app, "APP_USER_ID", "u1", raising=False)
    monkeypatch.setattr(frontend_app, "api_create_user", fake_create_user)
    monkeypatch.setattr(frontend_app, "api_get_bootstrap", fake_get_bootstrap, raising=False)

    result = asyncio.run(asyncio.wait_for(frontend_app.init_app(), timeout=1.0))

    assert started == first_stage
    assert result[0] == "u1"
    assert result[1] == "session-1"
    assert result[2] == [["知识.md", 2, "2026-07-28T00:00:00"]]
    assert result[3]["choices"] == [("知识.md (#document)", "document-1")]
    assert result[5] == [{"role": "user", "content": "历史问题"}]
    assert result[6]["choices"] == [("笔记", 1)]
    assert result[6]["value"] == 1
    assert result[7] == [("笔记", 1)]
    assert result[8] == "就绪"
    assert result[9:14] == (
        1,
        "笔记",
        "笔记正文",
        "✓ 已保存",
        frontend_app.note_snapshot("笔记", "笔记正文"),
    )


def test_session_switch_only_runs_for_user_input():
    from frontend import app as frontend_app

    ui = frontend_app.build_ui()
    dependency = next(
        item for item in ui.config["dependencies"]
        if item["api_name"] == "on_switch_session"
    )

    assert dependency["targets"][0][1] == "input"


def test_note_autosave_is_single_debounced_event():
    from frontend import app as frontend_app

    ui = frontend_app.build_ui()
    dependencies = [
        item for item in ui.config["dependencies"]
        if item["api_name"] == "save_note_if_dirty"
    ]

    assert len(dependencies) == 1
    block_fn = ui.fns[dependencies[0]["id"]]
    assert block_fn.concurrency_id == "note-autosave"
    assert block_fn.concurrency_limit == 1
    assert dependencies[0]["trigger_mode"] == "always_last"


def test_note_snapshot_does_not_depend_on_a_text_delimiter():
    from frontend import app as frontend_app

    first = frontend_app.note_snapshot("a|||b", "c")
    second = frontend_app.note_snapshot("a", "b|||c")

    assert first != second


def test_note_save_skips_unchanged_content(monkeypatch):
    from frontend import app as frontend_app

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("内容没有变化时不应发送请求")

    monkeypatch.setattr(frontend_app, "api_save_note", fail_if_called)
    snapshot = frontend_app.note_snapshot("标题", "正文")

    result = asyncio.run(
        frontend_app.persist_note_if_dirty("u1", 1, "标题", "正文", snapshot)
    )

    assert result == (True, "已保存", snapshot)


def test_note_save_accepts_title_only_draft(monkeypatch):
    from frontend import app as frontend_app

    captured = {}

    async def fake_save(user_id, concept, content, note_id):
        captured.update(
            user_id=user_id,
            concept=concept,
            content=content,
            note_id=note_id,
        )
        return "更新成功", note_id

    monkeypatch.setattr(frontend_app, "api_save_note", fake_save)

    result = asyncio.run(
        frontend_app.persist_note_if_dirty("u1", 1, "只有标题", "", "")
    )

    assert result[0] is True
    assert result[1] == "已保存"
    assert captured == {
        "user_id": "u1",
        "concept": "只有标题",
        "content": "",
        "note_id": 1,
    }


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
