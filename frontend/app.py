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
        resp = await client.get(f"{API_URL}/api/v1/notes", params={"user_id": user_id})
        resp.raise_for_status()
        notes = resp.json()
    return [(n["concept"] or "无标题", n["id"]) for n in notes]


async def api_save_note(user_id: str, concept: str, content: str, note_id) -> tuple[str, int | None]:
    """返回 (状态消息, 笔记ID)"""
    async with _api_client(timeout=httpx.Timeout(60.0)) as client:
        if note_id:
            resp = await client.put(
                f"{API_URL}/api/v1/notes/{note_id}",
                params={"user_id": user_id},
                json={"concept": concept, "content": content},
            )
            if resp.status_code == 409:
                return f"已有类似笔记: {resp.json().get('detail', '')}", note_id
            resp.raise_for_status()
            return "更新成功", note_id
        else:
            resp = await client.post(
                f"{API_URL}/api/v1/notes",
                json={"user_id": user_id, "concept": concept, "content": content},
            )
            if resp.status_code == 409:
                return f"已有类似笔记: {resp.json().get('detail', '')}", None
            resp.raise_for_status()
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
            gr.update(choices=notes),
            notes,
            "就绪",
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
        )


# ---- Gradio 界面 ----

def build_ui():
    with gr.Blocks(title="AI 知识库问答", css="""
        .note-sidebar { max-height: 520px; flex-wrap: nowrap !important; }
        .note-list { max-height: 440px; overflow-y: auto; }
        .note-list label { display: flex !important; width: 100% !important;
            padding: 6px 10px !important; border-radius: 4px !important;
            cursor: pointer !important; border: none !important;
            background: transparent !important; transition: background 0.15s !important; }
        .note-list label:hover { background: rgba(0,0,0,0.04) !important; }
        .note-list input[type="radio"] { display: none !important; }
        .note-list label:has(input:checked) { background: rgba(0,0,0,0.06) !important; font-weight: 500 !important; }
        .note-list span { font-size: 14px !important; }
    """) as app:
        user_id_state = gr.State("")
        session_id_state = gr.State("")
        note_id_state = gr.State(None)
        _expected_content = gr.State("")  # 加载笔记时记录内容快照，防误触发 auto_save
        _notes_choices = gr.State([])  # 当前笔记列表选项

        with gr.Tab("💬 对话"):
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

        with gr.Tab("📝 笔记"):
            with gr.Row():
                # 左侧：笔记列表（Notion 风格）
                with gr.Column(scale=1, min_width=200, elem_classes=["note-sidebar"]):
                    with gr.Row():
                        new_note_btn = gr.Button("＋ 新建笔记", variant="primary", size="sm")
                        delete_note_btn = gr.Button("🗑", variant="stop", size="sm")
                    note_list = gr.Radio(label="笔记列表", choices=[], interactive=True,
                                         elem_classes=["note-list"])

                # 右侧：编辑区（始终可编辑，自动保存）
                with gr.Column(scale=4):
                    concept_input = gr.Textbox(
                        label="标题", placeholder="选择笔记或点新建...",
                        interactive=True,
                    )
                    content_input = gr.Textbox(
                        label="内容",
                        lines=25,
                        placeholder="开始输入...",
                        interactive=True,
                    )
                    note_status = gr.Markdown("")

        # ---- 事件绑定 ----

        # 页面加载：首屏数据由 init_app 在同一阶段并行获取。
        app.load(
            fn=init_app,
            outputs=[user_id_state, session_id_state, docs_table, doc_dropdown,
                     session_dropdown, chatbot, note_list, _notes_choices, status_md],
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

        # 选择笔记 → 加载到编辑器
        async def on_select_note(note_id_val, user_id):
            if not note_id_val:
                return None, "", "", "", ""
            if note_id_val == -1:
                return None, "", "", "新笔记，开始输入即可保存", "|||"
            async with _api_client() as client:
                resp = await client.get(f"{API_URL}/api/v1/notes", params={"user_id": user_id})
                resp.raise_for_status()
                notes = resp.json()
            for n in notes:
                if n["id"] == note_id_val:
                    concept = n.get("concept", "")
                    content = n["content"]
                    return n["id"], concept, content, "", f"{concept}|||{content}"
            return None, "", "", "笔记未找到", ""

        note_list.change(
            on_select_note,
            inputs=[note_list, user_id_state],
            outputs=[note_id_state, concept_input, content_input, note_status, _expected_content],
        )

        # 新建笔记 → 本地列表添加，不调 API（避免 10 秒等待）
        def on_new_note(current_choices):
            # 已有未保存的临时笔记（-1）时，直接选中它，避免重复创建
            choices = current_choices or []
            if any(v == -1 for _, v in choices):
                return (None, "", "", "已有未保存的新笔记，请先编辑或删除",
                        gr.update(), gr.update(choices=choices, value=-1), choices)
            new_choices = [("📝 新笔记", -1)] + choices
            return (None, "", "", "新笔记，开始输入即可保存",
                    "|||", gr.update(choices=new_choices, value=-1), new_choices)

        new_note_btn.click(
            on_new_note,
            inputs=[_notes_choices],
            outputs=[note_id_state, concept_input, content_input, note_status, _expected_content, note_list, _notes_choices],
        )

        # 自动保存（show_progress=hidden 隐藏加载动画）
        async def auto_save(user_id, concept, content, note_id_val, expected):
            current = f"{concept}|||{content}"
            if current == expected:
                return "", note_id_val, expected, gr.update(), gr.update()
            if not user_id or not content.strip():
                return "内容不能为空", note_id_val, expected, gr.update(), gr.update()
            real_id = None if note_id_val in (None, -1) else note_id_val
            try:
                status, saved_id = await api_save_note(user_id, concept, content, real_id)
                # 新笔记首次保存 → 刷新列表并选中刚保存的笔记
                if real_id is None and saved_id:
                    choices = await api_list_notes(user_id)
                    return f"✅ {status}", saved_id, current, gr.update(choices=choices, value=saved_id), choices
                return f"✅ {status}", saved_id, current, gr.update(), gr.update()
            except Exception as e:
                return f"❌ {e}", note_id_val, expected, gr.update(), gr.update()

        content_input.change(
            auto_save,
            inputs=[user_id_state, concept_input, content_input, note_id_state, _expected_content],
            outputs=[note_status, note_id_state, _expected_content, note_list, _notes_choices],
            trigger_mode="always_last",
            show_progress="hidden",
        )
        concept_input.change(
            auto_save,
            inputs=[user_id_state, concept_input, content_input, note_id_state, _expected_content],
            outputs=[note_status, note_id_state, _expected_content, note_list, _notes_choices],
            trigger_mode="always_last",
            show_progress="hidden",
        )

        # 删除笔记（从 note_id_state 读取，而非 note_list Radio）
        async def on_delete_note(note_id_val, user_id):
            if not note_id_val or note_id_val == -1:
                if user_id:
                    choices = await api_list_notes(user_id)
                    return "已删除", None, "", "", gr.update(choices=choices, value=None), "", choices
                return "请选择笔记", None, "", "", gr.update(), "", []
            try:
                status = await api_delete_note(user_id, note_id_val)
            except Exception as e:
                if user_id:
                    choices = await api_list_notes(user_id)
                    return f"删除失败: {e}", None, "", "", gr.update(choices=choices, value=None), "", choices
                return f"删除失败: {e}", None, "", "", gr.update(), "", []
            choices = await api_list_notes(user_id)
            return status, None, "", "", gr.update(choices=choices, value=None), "", choices

        delete_note_btn.click(
            on_delete_note,
            inputs=[note_id_state, user_id_state],
            outputs=[note_status, note_id_state, concept_input, content_input, note_list, _expected_content, _notes_choices],
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
    ui.launch(server_name=GRADIO_HOST, server_port=7860)


if __name__ == "__main__":
    run_frontend()
