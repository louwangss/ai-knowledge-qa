"""Gradio 前端：薄 UI 层，通过 HTTP 消费 FastAPI SSE"""
import os
import json
import asyncio
import uuid
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import gradio as gr
from dotenv import load_dotenv

load_dotenv()
API_URL = os.getenv("API_URL", "http://localhost:8000")
APP_ACCESS_TOKEN = os.getenv("APP_ACCESS_TOKEN", "").strip()
APP_USER_ID = os.getenv("APP_USER_ID", "default-user").strip()
GRADIO_HOST = os.getenv("GRADIO_HOST", "127.0.0.1").strip()

# Gradio 没有原生 debounce 参数；这是可调的体验型默认值，后续按真实输入轨迹校准。
NOTE_AUTOSAVE_DELAY_MS = 800


# ---- API 调用 ----

def validate_frontend_config(access_token: str, server_name: str) -> None:
    """前端必须持有 API token，监听地址也不能为空。"""
    if not access_token:
        raise RuntimeError("环境变量 APP_ACCESS_TOKEN 未设置")
    if not server_name:
        raise RuntimeError("环境变量 GRADIO_HOST 不能为空")


validate_frontend_config(APP_ACCESS_TOKEN, GRADIO_HOST)
if not APP_USER_ID:
    raise RuntimeError("环境变量 APP_USER_ID 不能为空")


def _api_client(timeout=None) -> httpx.AsyncClient:
    """创建统一携带认证头的 API 客户端。"""
    kwargs = {
        "headers": {"Authorization": f"Bearer {APP_ACCESS_TOKEN}"},
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    return httpx.AsyncClient(**kwargs)


async def api_create_user() -> str:
    async with _api_client() as client:
        resp = await client.post(f"{API_URL}/api/v1/users", json={"username": "user"})
        resp.raise_for_status()
        return resp.json()["id"]


async def api_create_session(user_id: str) -> str:
    async with _api_client() as client:
        resp = await client.post(f"{API_URL}/api/v1/sessions", json={"user_id": user_id})
        resp.raise_for_status()
        return resp.json()["id"]


async def api_list_sessions(user_id: str) -> list[dict]:
    async with _api_client() as client:
        resp = await client.get(f"{API_URL}/api/v1/sessions", params={"user_id": user_id})
        resp.raise_for_status()
        return resp.json()


async def api_delete_session(user_id: str, session_id: str) -> str:
    async with _api_client() as client:
        resp = await client.delete(
            f"{API_URL}/api/v1/sessions/{session_id}",
            params={"user_id": user_id},
        )
        resp.raise_for_status()
        return "删除成功"


async def api_get_chat_history(user_id: str, session_id: str) -> list[dict]:
    async with _api_client() as client:
        resp = await client.get(
            f"{API_URL}/api/v1/chat/history",
            params={"user_id": user_id, "session_id": session_id},
        )
        resp.raise_for_status()
        return resp.json()


async def api_upload_document(user_id: str, file) -> str:
    async with _api_client(timeout=httpx.Timeout(60.0)) as client:
        with open(file.name, "rb") as f:
            resp = await client.post(
                f"{API_URL}/api/v1/documents",
                files={"file": (os.path.basename(file.name), f)},
                data={"user_id": user_id},
            )
        if resp.status_code == 409:
            return f"文档已存在: {resp.json().get('detail', '')}"
        resp.raise_for_status()
        data = resp.json()
        return f"文档《{data['filename']}》已就绪（{data.get('chunk_count', 0)} 个片段）"


async def _api_fetch_documents(user_id: str) -> list[dict]:
    async with _api_client() as client:
        resp = await client.get(f"{API_URL}/api/v1/documents", params={"user_id": user_id})
        resp.raise_for_status()
        return resp.json()


async def api_get_bootstrap(user_id: str) -> dict:
    """一次获取 Gradio 首屏所需的只读数据。"""
    async with _api_client() as client:
        resp = await client.get(
            f"{API_URL}/api/v1/bootstrap",
            params={"user_id": user_id},
        )
        resp.raise_for_status()
        return resp.json()


def _documents_table(docs: list[dict]) -> list[list]:
    return [[d["filename"], d.get("chunk_count", 0), d["created_at"]] for d in docs]


def _documents_dropdown(docs: list[dict]) -> dict:
    choices = [(f"{d['filename']} (#{d['id'][:8]})", d["id"]) for d in docs]
    return gr.update(choices=choices)


async def api_list_documents(user_id: str) -> list[list]:
    return _documents_table(await _api_fetch_documents(user_id))


async def api_delete_document(user_id: str, doc_id: str) -> str:
    if not doc_id or doc_id == "无":
        return "请先选择文档"
    async with _api_client() as client:
        resp = await client.delete(
            f"{API_URL}/api/v1/documents/{doc_id}",
            params={"user_id": user_id},
        )
        resp.raise_for_status()
        return "删除成功"


async def api_list_notes(user_id: str) -> list[tuple]:
    """获取笔记列表，返回 Radio 选项 [(label, value), ...]"""
    async with _api_client() as client:
        resp = await client.get(
            f"{API_URL}/api/v1/notes/summaries",
            params={"user_id": user_id},
        )
        resp.raise_for_status()
        notes = resp.json()
    return [(n["concept"] or "无标题", n["id"]) for n in notes]


async def api_get_note(user_id: str, note_id: int) -> dict | None:
    """按 ID 读取单篇笔记，避免切换时下载全部正文。"""
    async with _api_client() as client:
        resp = await client.get(
            f"{API_URL}/api/v1/notes/{note_id}",
            params={"user_id": user_id},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


async def api_save_note(user_id: str, concept: str, content: str, note_id) -> tuple[str, int | None]:
    """返回 (状态消息, 笔记ID)"""
    async with _api_client(timeout=httpx.Timeout(60.0)) as client:
        if note_id:
            resp = await client.put(
                f"{API_URL}/api/v1/notes/{note_id}",
                params={"user_id": user_id},
                json={"concept": concept, "content": content},
            )
            if resp.is_error:
                detail = resp.json().get("detail", "保存失败")
                raise RuntimeError(detail if isinstance(detail, str) else "保存失败")
            return "更新成功", note_id
        else:
            resp = await client.post(
                f"{API_URL}/api/v1/notes",
                json={"user_id": user_id, "concept": concept, "content": content},
            )
            if resp.is_error:
                detail = resp.json().get("detail", "创建失败")
                raise RuntimeError(detail if isinstance(detail, str) else "创建失败")
            new_id = resp.json().get("id")
            return "保存成功", new_id


async def api_delete_note(user_id: str, note_id) -> str:
    if not note_id:
        return "请先选择笔记"
    async with _api_client() as client:
        resp = await client.delete(
            f"{API_URL}/api/v1/notes/{note_id}",
            params={"user_id": user_id},
        )
        resp.raise_for_status()
        return "删除成功"


def note_snapshot(concept: str | None, content: str | None) -> str:
    """生成无分隔符歧义的编辑快照，用于跳过无变化保存。"""
    return json.dumps([concept or "", content or ""], ensure_ascii=False, separators=(",", ":"))


async def persist_note_if_dirty(
    user_id: str,
    note_id: int | None,
    concept: str,
    content: str,
    expected_snapshot: str,
) -> tuple[bool, str, str]:
    """只保存发生变化的当前笔记，并保留失败前的快照用于重试。"""
    current_snapshot = note_snapshot(concept, content)
    if current_snapshot == expected_snapshot:
        return True, "已保存", expected_snapshot
    if not user_id or not note_id:
        return False, "笔记尚未就绪", expected_snapshot
    try:
        await api_save_note(user_id, concept, content, note_id)
    except Exception:
        return False, "保存失败，内容仍保留在编辑器中", expected_snapshot
    return True, "已保存", current_snapshot


async def api_get_documents_dropdown(user_id: str) -> dict:
    """获取文档下拉选项"""
    return _documents_dropdown(await _api_fetch_documents(user_id))


# ---- SSE 解析 ----

async def parse_sse_stream(resp):
    """解析 SSE 流，逐行读取 event: 和 data: 行"""
    event_type = None
    data_lines = []
    async for line in resp.aiter_lines():
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: "):
            data_lines.append(line[6:])
        elif line == "":
            # 空行 = 一条消息结束
            if event_type and data_lines:
                data_str = "\n".join(data_lines)
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = {"content": data_str}
                yield {"type": event_type, "content": data.get("content", "")}
            event_type = None
            data_lines = []


def format_sources(sources: list) -> str:
    if not sources:
        return ""
    lines = ["\n\n---\n**来源：**"]
    for s in sources:
        lines.append(f"- {s['source']}（相关度: {s.get('score', 0):.2f}）")
    return "\n".join(lines)


# ---- Chat 事件处理 ----

async def chat_fn(message, history, mode, user_id, session_id):
    """Gradio 后端：调 FastAPI SSE，翻译成 Gradio 渲染"""
    if not user_id:
        yield history, "", "请等待初始化完成"
        return
    if not session_id:
        yield history, "", "会话未创建，请刷新页面"
        return

    # 构造完整消息列表（gr.Chatbot type="messages" 格式）
    assistant_msg = {"role": "assistant", "content": ""}
    chatbot_history = history + [{"role": "user", "content": message}, assistant_msg]
    accumulated = ""
    collected_sources = []
    first_token = True

    yield chatbot_history, "", ""  # 先显示用户消息

    async with _api_client(timeout=httpx.Timeout(120.0)) as client:
        async with client.stream(
            "POST",
            f"{API_URL}/api/v1/chat",
            json={
                "user_id": user_id,
                "session_id": session_id,
                "message": message,
                "mode": mode,
            },
        ) as resp:
            resp.raise_for_status()
            async for event in parse_sse_stream(resp):
                if event["type"] == "status":
                    yield chatbot_history, event["content"], ""
                elif event["type"] == "token":
                    if first_token:
                        first_token = False
                    accumulated += event["content"]
                    assistant_msg["content"] = accumulated
                    yield chatbot_history, "", ""
                elif event["type"] == "sources":
                    collected_sources = event["content"]
                elif event["type"] == "done":
                    if collected_sources:
                        assistant_msg["content"] = accumulated + format_sources(collected_sources)
                    yield chatbot_history, "", ""
                    return
                elif event["type"] == "error":
                    assistant_msg["content"] = accumulated + f"\n\n⚠️ {event['content']}"
                    yield chatbot_history, "", ""
                    return


# ---- 初始化 ----

async def init_app():
    """页面加载时创建用户，加载会话列表和最近会话的聊天记录"""
    try:
        # 用户创建是幂等写；首屏只读快照可使用同一固定 ID 与其并行。
        user_id, bootstrap = await asyncio.gather(
            api_create_user(),
            api_get_bootstrap(APP_USER_ID),
        )
        document_data = bootstrap["documents"]
        sessions = bootstrap["sessions"]
        notes = [
            (note.get("concept") or "无标题", note["id"])
            for note in bootstrap["notes"]
        ]
        active_note = bootstrap.get("active_note")
        if active_note:
            active_note_id = active_note["id"]
            active_concept = active_note.get("concept") or ""
            active_content = active_note.get("content") or ""
            active_status = "✓ 已保存"
        else:
            active_note_id = None
            active_concept = ""
            active_content = ""
            active_status = "请选择或新建一篇笔记"
        docs = _documents_table(document_data)
        doc_choices = _documents_dropdown(document_data)

        session_items = [(s["title"] or "新会话", s["id"]) for s in sessions]

        # 有会话则选最近的，没有则创建
        if sessions:
            session_id = sessions[0]["id"]
            history = bootstrap["history"]
            chatbot_init = [{"role": h["role"], "content": h["content"]} for h in history]
        else:
            session_id = await api_create_session(user_id)
            chatbot_init = []
            session_items = [("新会话", session_id)]

        return (
            user_id,
            session_id,
            docs,
            doc_choices,
            gr.update(choices=session_items, value=session_id),
            chatbot_init,
            gr.update(choices=notes, value=active_note_id),
            notes,
            "就绪",
            active_note_id,
            active_concept,
            active_content,
            active_status,
            note_snapshot(active_concept, active_content),
        )
    except Exception:
        return (
            "",
            "",
            [],
            gr.update(),
            gr.update(),
            [],
            gr.update(),
            [],
            "初始化失败，请检查后端服务和认证配置",
            None,
            "",
            "",
            "初始化失败",
            note_snapshot("", ""),
        )


# ---- Gradio 界面 ----

APP_CSS = """
    :root {
        --app-bg: #f4f5f7;
        --surface: #ffffff;
        --surface-muted: #f7f7f5;
        --text-primary: #202124;
        --text-secondary: #6b7280;
        --border: #e5e7eb;
        --accent: #0f766e;
        --accent-hover: #115e59;
        --danger: #b42318;
    }
    body, .gradio-container { background: var(--app-bg) !important; }
    .gradio-container { max-width: none !important; padding: 0 !important; }
    .app-header {
        display: flex; align-items: center; justify-content: space-between;
        min-height: 4rem; padding: 0 2rem; background: var(--surface);
        border-bottom: 1px solid var(--border);
    }
    .app-brand { display: flex; align-items: center; gap: .75rem; }
    .app-mark {
        display: grid; place-items: center; width: 2rem; height: 2rem;
        border-radius: .5rem; background: var(--accent); color: white;
        font-size: .875rem; font-weight: 700;
    }
    .app-title { color: var(--text-primary); font-size: 1rem; font-weight: 650; }
    .app-subtitle { color: var(--text-secondary); font-size: .75rem; }
    .service-state { color: #166534; font-size: .8125rem; }
    .workspace-tabs { max-width: 96rem; margin: 0 auto; padding: 0 1.5rem 1.5rem; }
    .workspace-tabs > .tab-nav { background: transparent !important; padding-top: .5rem; }
    button.primary { background: var(--accent) !important; border-color: var(--accent) !important; }
    button.primary:hover { background: var(--accent-hover) !important; }
    .notes-workspace {
        gap: 0 !important; min-height: calc(100vh - 9.5rem);
        overflow: hidden; border: 1px solid var(--border); border-radius: .75rem;
        background: var(--surface); box-shadow: 0 1px 2px rgba(16, 24, 40, .04);
    }
    .note-sidebar {
        min-height: calc(100vh - 9.5rem); padding: 1rem !important;
        background: var(--surface-muted); border-right: 1px solid var(--border);
        flex-wrap: nowrap !important;
    }
    .note-sidebar > div, .note-sidebar .form { background: var(--surface-muted) !important; }
    .note-sidebar-header { align-items: center !important; margin-bottom: .5rem; }
    .note-sidebar-title h2 { margin: 0 !important; font-size: 1rem !important; }
    .new-note-button { min-width: 2.5rem !important; max-width: 2.5rem !important; }
    .note-list { flex: 1; overflow-y: auto; border: 0 !important; background: transparent !important; }
    .note-list > div, .note-list .wrap, .note-list .form {
        border: 0 !important; background: transparent !important; box-shadow: none !important;
    }
    .note-list label {
        display: flex !important; width: 100% !important; min-height: 2.5rem;
        align-items: center !important; padding: .625rem .75rem !important;
        border: 0 !important; border-radius: .5rem !important;
        background: transparent !important; cursor: pointer !important;
        transition: background-color .15s ease !important;
    }
    .note-list label:hover { background: #ececea !important; }
    .note-list label:has(input:checked) {
        background: #e2e8e7 !important; color: #134e4a !important; font-weight: 600 !important;
    }
    .note-list input[type="radio"] { display: none !important; }
    .note-list span { overflow: hidden; font-size: .875rem !important; text-overflow: ellipsis; white-space: nowrap; }
    .note-editor { min-height: calc(100vh - 9.5rem); padding: 1.25rem 3rem 3rem !important; }
    .note-editor-toolbar { align-items: center !important; min-height: 2.5rem; }
    .note-editor-actions {
        flex: 0 0 auto !important; flex-wrap: nowrap !important;
        justify-content: flex-end !important; align-items: center !important;
    }
    .note-editor-actions button { flex: 0 0 auto !important; width: auto !important; }
    #note-save-status { color: var(--text-secondary); font-size: .8125rem; }
    #note-save-status p { margin: 0 !important; }
    .note-delete-button { color: var(--text-secondary) !important; }
    .note-delete-button:hover { color: var(--danger) !important; border-color: #fda29b !important; }
    #note-title { margin-top: 1.75rem; }
    #note-title, #note-title > div, #note-title .wrap,
    #note-content, #note-content > div, #note-content .wrap {
        border: 0 !important; background: transparent !important; box-shadow: none !important;
    }
    #note-title textarea, #note-title input {
        min-height: 3.5rem !important; padding: 0 !important; border: 0 !important;
        background: transparent !important; box-shadow: none !important;
        color: var(--text-primary) !important; font-size: 2rem !important;
        font-weight: 700 !important; line-height: 1.25 !important;
    }
    #note-content { margin-top: 1rem; }
    #note-content textarea {
        min-height: calc(100vh - 18rem) !important; padding: 0 !important;
        border: 0 !important; background: transparent !important; box-shadow: none !important;
        color: #374151 !important; font-size: 1rem !important; line-height: 1.75 !important;
        resize: none !important;
    }
    #note-title textarea:focus, #note-title input:focus, #note-content textarea:focus {
        outline: none !important; box-shadow: none !important;
    }
    .visually-hidden-trigger {
        position: fixed !important; left: -10000px !important; width: 1px !important;
        height: 1px !important; overflow: hidden !important; visibility: hidden !important;
    }
    footer { display: none !important; }
    @media (max-width: 48rem) {
        .app-header { padding: 0 1rem; }
        .app-subtitle, .service-state { display: none; }
        .workspace-tabs { padding: 0 .75rem .75rem; }
        .notes-workspace { min-height: auto; }
        .note-sidebar { min-height: auto; max-height: 16rem; border-right: 0; border-bottom: 1px solid var(--border); }
        .note-editor { min-height: 32rem; padding: 1rem 1.25rem 2rem !important; }
        #note-title textarea, #note-title input { font-size: 1.625rem !important; }
    }
"""


def build_ui():
    with gr.Blocks(title="AI 知识库问答", css=APP_CSS) as app:
        user_id_state = gr.State("")
        session_id_state = gr.State("")
        note_id_state = gr.State(None)
        _expected_content = gr.State(note_snapshot("", ""))
        _notes_choices = gr.State([])  # 当前笔记列表选项

        gr.HTML("""
            <header class="app-header">
                <div class="app-brand">
                    <span class="app-mark" aria-hidden="true">K</span>
                    <div>
                        <div class="app-title">知识助手</div>
                        <div class="app-subtitle">对话、资料与笔记集中管理</div>
                    </div>
                </div>
                <div class="service-state">● 本地服务已连接</div>
            </header>
        """, padding=False)

        with gr.Tabs(elem_classes=["workspace-tabs"]):
          with gr.Tab("对话"):
            with gr.Row():
                # 左侧：会话列表
                with gr.Column(scale=1, min_width=200):
                    new_session_btn = gr.Button("＋ 新建会话", variant="primary", size="sm")
                    session_dropdown = gr.Dropdown(
                        label="会话列表",
                        choices=[],
                        interactive=True,
                        scale=1,
                    )
                    delete_session_btn = gr.Button("删除选中会话", variant="stop", size="sm")
                    confirm_delete_session_btn = gr.Button("⚠️ 确认删除（不可恢复）", variant="stop", size="sm", visible=False)
                    cancel_delete_session_btn = gr.Button("取消", size="sm", visible=False)

                # 中间：聊天区
                with gr.Column(scale=3):
                    mode_radio = gr.Radio(
                        choices=[("普通问答", "normal"), ("深度研究（较慢，10~18 秒）", "deep")],
                        value="normal",
                        label="模式",
                    )
                    status_md = gr.Markdown("", elem_id="status-bar")
                    chatbot = gr.Chatbot(height=500, type="messages")
                    msg_input = gr.Textbox(
                        placeholder="输入问题...",
                        show_label=False,
                        scale=4,
                    )
                    send_btn = gr.Button("发送", variant="primary")

                # 右侧：文档区
                with gr.Column(scale=1):
                    gr.Markdown("### 📄 文档管理")
                    file_upload = gr.File(
                        label="上传文档",
                        file_types=[".pdf", ".docx", ".md", ".txt", ".html", ".htm"],
                    )
                    upload_status = gr.Markdown("")
                    docs_table = gr.Dataframe(
                        headers=["文件名", "切片数", "上传时间"],
                        datatype=["str", "number", "str"],
                        interactive=False,
                        wrap=True,
                    )
                    doc_dropdown = gr.Dropdown(label="选择文档删除", choices=[], interactive=True)
                    delete_doc_btn = gr.Button("删除文档", variant="stop")

          with gr.Tab("笔记"):
            with gr.Row(elem_classes=["notes-workspace"]):
                with gr.Column(scale=1, min_width=240, elem_classes=["note-sidebar"]):
                    with gr.Row(elem_classes=["note-sidebar-header"]):
                        gr.Markdown("## 我的笔记", elem_classes=["note-sidebar-title"])
                        new_note_btn = gr.Button(
                            "＋", variant="primary", size="sm",
                            elem_classes=["new-note-button"],
                        )
                    note_list = gr.Radio(
                        label="笔记列表",
                        show_label=False,
                        choices=[],
                        interactive=True,
                        elem_classes=["note-list"],
                    )

                with gr.Column(scale=4, elem_classes=["note-editor"]):
                    with gr.Row(elem_classes=["note-editor-toolbar"]):
                        note_status = gr.Markdown("已保存", elem_id="note-save-status")
                        with gr.Row(elem_classes=["note-editor-actions"], scale=0):
                            retry_note_btn = gr.Button(
                                "重试保存", size="sm", visible=False, scale=0, min_width=80,
                            )
                            delete_note_btn = gr.Button(
                                "删除", size="sm", visible=True,
                                elem_classes=["note-delete-button"], scale=0, min_width=64,
                            )
                            confirm_delete_note_btn = gr.Button(
                                "确认删除", variant="stop", size="sm", visible=False,
                                scale=0, min_width=80,
                            )
                            cancel_delete_note_btn = gr.Button(
                                "取消", size="sm", visible=False, scale=0, min_width=64,
                            )
                    concept_input = gr.Textbox(
                        placeholder="无标题",
                        show_label=False,
                        container=False,
                        lines=1,
                        max_lines=1,
                        max_length=100,
                        interactive=True,
                        elem_id="note-title",
                    )
                    content_input = gr.Textbox(
                        placeholder="开始记录你的想法……",
                        show_label=False,
                        container=False,
                        lines=22,
                        interactive=True,
                        elem_id="note-content",
                    )
                    autosave_trigger = gr.Button(
                        "自动保存",
                        elem_id="note-autosave-trigger",
                        elem_classes=["visually-hidden-trigger"],
                    )

        # ---- 事件绑定 ----

        # 页面加载：首屏数据由 init_app 在同一阶段并行获取。
        app.load(
            fn=init_app,
            outputs=[user_id_state, session_id_state, docs_table, doc_dropdown,
                     session_dropdown, chatbot, note_list, _notes_choices, status_md,
                     note_id_state, concept_input, content_input, note_status,
                     _expected_content],
        )

        # 发送消息（完成后刷新会话列表，更新标题）
        async def refresh_sessions(user_id, session_id):
            if not user_id:
                return gr.update()
            sessions = await api_list_sessions(user_id)
            items = [(s["title"] or "新会话", s["id"]) for s in sessions]
            return gr.update(choices=items, value=session_id)

        send_btn.click(
            chat_fn,
            inputs=[msg_input, chatbot, mode_radio, user_id_state, session_id_state],
            outputs=[chatbot, status_md, msg_input],
        ).then(
            refresh_sessions,
            inputs=[user_id_state, session_id_state],
            outputs=[session_dropdown],
        )
        msg_input.submit(
            chat_fn,
            inputs=[msg_input, chatbot, mode_radio, user_id_state, session_id_state],
            outputs=[chatbot, status_md, msg_input],
        ).then(
            refresh_sessions,
            inputs=[user_id_state, session_id_state],
            outputs=[session_dropdown],
        )

        # 文档上传
        async def on_upload(file, user_id):
            if not file or not user_id:
                return "请先等待初始化", [], gr.update()
            status = await api_upload_document(user_id, file)
            docs = await api_list_documents(user_id)
            doc_choices = await api_get_documents_dropdown(user_id)
            return status, docs, doc_choices

        file_upload.change(
            on_upload,
            inputs=[file_upload, user_id_state],
            outputs=[upload_status, docs_table, doc_dropdown],
        )

        # 删除文档
        async def on_delete_doc(doc_id, user_id):
            if not doc_id:
                return "请选择文档", [], gr.update()
            status = await api_delete_document(user_id, doc_id)
            docs = await api_list_documents(user_id)
            doc_choices = await api_get_documents_dropdown(user_id)
            return status, docs, doc_choices

        delete_doc_btn.click(
            on_delete_doc,
            inputs=[doc_dropdown, user_id_state],
            outputs=[upload_status, docs_table, doc_dropdown],
        )

        def _updated_note_choices(choices, note_id_val, concept):
            label = (concept or "").strip() or "无标题"
            return [
                (label if value == note_id_val else old_label, value)
                for old_label, value in (choices or [])
            ]

        async def _load_note(note_id_val, user_id):
            if not note_id_val or not user_id:
                return None, "", "", "请选择或新建一篇笔记", note_snapshot("", "")
            note = await api_get_note(user_id, note_id_val)
            if note is None:
                return None, "", "", "笔记未找到", note_snapshot("", "")
            concept = note.get("concept") or ""
            content = note.get("content") or ""
            return note["id"], concept, content, "已保存", note_snapshot(concept, content)

        async def _on_auto_save(user_id, note_id_val, concept, content, expected, choices):
            success, status, snapshot = await persist_note_if_dirty(
                user_id, note_id_val, concept, content, expected,
            )
            if success:
                updated_choices = _updated_note_choices(choices, note_id_val, concept)
                return (
                    f"✓ {status}", snapshot, gr.update(visible=False),
                    gr.update(choices=updated_choices, value=note_id_val), updated_choices,
                )
            return (
                f"⚠ {status}", snapshot, gr.update(visible=True),
                gr.update(), gr.update(),
            )

        autosave_trigger.click(
            _on_auto_save,
            inputs=[user_id_state, note_id_state, concept_input, content_input,
                    _expected_content, _notes_choices],
            outputs=[note_status, _expected_content, retry_note_btn, note_list, _notes_choices],
            api_name="save_note_if_dirty",
            trigger_mode="always_last",
            concurrency_limit=1,
            concurrency_id="note-autosave",
            show_progress="hidden",
        )
        retry_note_btn.click(
            _on_auto_save,
            inputs=[user_id_state, note_id_state, concept_input, content_input,
                    _expected_content, _notes_choices],
            outputs=[note_status, _expected_content, retry_note_btn, note_list, _notes_choices],
            api_name=False,
            trigger_mode="always_last",
            concurrency_limit=1,
            concurrency_id="note-autosave",
            show_progress="hidden",
        )

        schedule_autosave_js = f"""
            () => {{
                const statusRoot = document.querySelector('#note-save-status');
                const statusText = statusRoot?.querySelector('p') || statusRoot;
                if (statusText) statusText.textContent = '● 未保存';
                window.clearTimeout(window.__noteAutosaveTimer);
                window.__noteAutosaveTimer = window.setTimeout(() => {{
                    const trigger = document.querySelector(
                        '#note-autosave-trigger button, button#note-autosave-trigger'
                    );
                    if (trigger) trigger.click();
                }}, {NOTE_AUTOSAVE_DELAY_MS});
            }}
        """
        concept_input.input(fn=None, js=schedule_autosave_js, queue=False)
        content_input.input(fn=None, js=schedule_autosave_js, queue=False)

        async def on_switch_note(
            selected_id, user_id, current_id, concept, content, expected, choices,
        ):
            success, status, snapshot = await persist_note_if_dirty(
                user_id, current_id, concept, content, expected,
            )
            if not success:
                return (
                    current_id, concept, content, f"⚠ {status}", snapshot,
                    gr.update(choices=choices, value=current_id), choices,
                    gr.update(visible=True),
                )
            updated_choices = _updated_note_choices(choices, current_id, concept)
            note_id_val, next_concept, next_content, next_status, next_snapshot = await _load_note(
                selected_id, user_id,
            )
            return (
                note_id_val, next_concept, next_content, next_status, next_snapshot,
                gr.update(choices=updated_choices, value=note_id_val), updated_choices,
                gr.update(visible=False),
            )

        note_list.input(
            on_switch_note,
            inputs=[note_list, user_id_state, note_id_state, concept_input, content_input,
                    _expected_content, _notes_choices],
            outputs=[note_id_state, concept_input, content_input, note_status,
                     _expected_content, note_list, _notes_choices, retry_note_btn],
            api_name=False,
            concurrency_limit=1,
            concurrency_id="note-autosave",
        )

        async def on_new_note(user_id, current_id, concept, content, expected, choices):
            success, status, snapshot = await persist_note_if_dirty(
                user_id, current_id, concept, content, expected,
            )
            if not success:
                return (
                    current_id, concept, content, f"⚠ {status}", snapshot,
                    gr.update(choices=choices, value=current_id), choices,
                    gr.update(visible=True),
                )
            updated_choices = _updated_note_choices(choices, current_id, concept)
            try:
                _, new_id = await api_save_note(user_id, "", "", None)
                refreshed_choices = await api_list_notes(user_id)
            except Exception:
                return (
                    current_id, concept, content, "⚠ 新建失败，请重试", snapshot,
                    gr.update(choices=updated_choices, value=current_id), updated_choices,
                    gr.update(visible=False),
                )
            return (
                new_id, "", "", "新笔记 · 已保存", note_snapshot("", ""),
                gr.update(choices=refreshed_choices, value=new_id), refreshed_choices,
                gr.update(visible=False),
            )

        new_note_btn.click(
            on_new_note,
            inputs=[user_id_state, note_id_state, concept_input, content_input,
                    _expected_content, _notes_choices],
            outputs=[note_id_state, concept_input, content_input, note_status,
                     _expected_content, note_list, _notes_choices, retry_note_btn],
            api_name=False,
            concurrency_limit=1,
            concurrency_id="note-autosave",
        )

        def on_delete_note_click(note_id_val):
            if not note_id_val:
                return "请选择笔记", gr.update(), gr.update(), gr.update()
            return (
                "删除后无法恢复",
                gr.update(visible=False),
                gr.update(visible=True),
                gr.update(visible=True),
            )

        delete_note_btn.click(
            on_delete_note_click,
            inputs=[note_id_state],
            outputs=[note_status, delete_note_btn, confirm_delete_note_btn, cancel_delete_note_btn],
        )

        def on_cancel_delete_note():
            return (
                "已取消删除",
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
            )

        cancel_delete_note_btn.click(
            on_cancel_delete_note,
            outputs=[note_status, delete_note_btn, confirm_delete_note_btn, cancel_delete_note_btn],
        )

        async def on_confirm_delete_note(note_id_val, user_id):
            if not note_id_val or not user_id:
                return (
                    None, "", "", "请选择笔记", note_snapshot("", ""),
                    gr.update(), gr.update(), [],
                    gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
                )
            try:
                await api_delete_note(user_id, note_id_val)
                choices = await api_list_notes(user_id)
                next_id = choices[0][1] if choices else None
                loaded = await _load_note(next_id, user_id)
                return (
                    loaded[0], loaded[1], loaded[2],
                    "笔记已删除" if next_id else "笔记已删除，新建一篇开始记录",
                    loaded[4], gr.update(choices=choices, value=next_id),
                    gr.update(visible=False), choices,
                    gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
                )
            except Exception:
                return (
                    note_id_val, gr.update(), gr.update(), "删除失败，请重试", gr.update(),
                    gr.update(), gr.update(), gr.update(),
                    gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
                )

        confirm_delete_note_btn.click(
            on_confirm_delete_note,
            inputs=[note_id_state, user_id_state],
            outputs=[note_id_state, concept_input, content_input, note_status,
                     _expected_content, note_list, retry_note_btn, _notes_choices,
                     delete_note_btn, confirm_delete_note_btn, cancel_delete_note_btn],
            concurrency_limit=1,
            concurrency_id="note-autosave",
        )

        # ---- 会话管理事件 ----

        # 新建会话
        async def on_new_session(user_id):
            if not user_id:
                return "", gr.update(), [], ""
            new_id = await api_create_session(user_id)
            sessions = await api_list_sessions(user_id)
            session_items = [(s["title"] or "新会话", s["id"]) for s in sessions]
            return new_id, gr.update(choices=session_items, value=new_id), [], "新会话已创建"

        new_session_btn.click(
            on_new_session,
            inputs=[user_id_state],
            outputs=[session_id_state, session_dropdown, chatbot, status_md],
        )

        # 切换会话（加载历史记录 + 同步 session_id_state）
        async def on_switch_session(session_id, user_id):
            if not session_id or not user_id:
                return "", [], "已切换会话"
            history = await api_get_chat_history(user_id, session_id)
            chatbot_data = [{"role": h["role"], "content": h["content"]} for h in history]
            return session_id, chatbot_data, "已切换会话"

        session_dropdown.input(
            on_switch_session,
            inputs=[session_dropdown, user_id_state],
            outputs=[session_id_state, chatbot, status_md],
        )

        # 点击删除按钮 → 显示确认/取消
        def on_delete_session_click():
            return gr.update(visible=True), gr.update(visible=True), gr.update(visible=False)

        delete_session_btn.click(
            on_delete_session_click,
            outputs=[confirm_delete_session_btn, cancel_delete_session_btn, delete_session_btn],
        )

        # 确认删除
        async def on_confirm_delete_session(session_id, user_id):
            if not session_id or not user_id:
                return ("", gr.update(), [], "请选择会话",
                        gr.update(visible=True), gr.update(visible=False), gr.update(visible=False))
            await api_delete_session(user_id, session_id)
            sessions = await api_list_sessions(user_id)
            if sessions:
                session_items = [(s["title"] or "新会话", s["id"]) for s in sessions]
                next_id = sessions[0]["id"]
                history = await api_get_chat_history(user_id, next_id)
                chatbot_data = [{"role": h["role"], "content": h["content"]} for h in history]
                return (next_id, gr.update(choices=session_items, value=next_id), chatbot_data, "会话已删除",
                        gr.update(visible=False), gr.update(visible=False), gr.update(visible=True))
            else:
                new_id = await api_create_session(user_id)
                return (new_id, gr.update(choices=[("新会话", new_id)], value=new_id), [], "会话已删除，已创建新会话",
                        gr.update(visible=False), gr.update(visible=False), gr.update(visible=True))

        confirm_delete_session_btn.click(
            on_confirm_delete_session,
            inputs=[session_dropdown, user_id_state],
            outputs=[session_id_state, session_dropdown, chatbot, status_md,
                     confirm_delete_session_btn, cancel_delete_session_btn, delete_session_btn],
        )

        # 取消删除
        def on_cancel_delete_session():
            return gr.update(visible=False), gr.update(visible=False), gr.update(visible=True)

        cancel_delete_session_btn.click(
            on_cancel_delete_session,
            outputs=[confirm_delete_session_btn, cancel_delete_session_btn, delete_session_btn],
        )

    return app


def run_frontend():
    """使用配置的监听地址启动 Gradio。"""
    ui = build_ui()
    ui.launch(server_name=GRADIO_HOST, server_port=7860, pwa=False)


if __name__ == "__main__":
    run_frontend()
