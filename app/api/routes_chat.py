"""Chat 路由：POST /api/v1/chat — SSE 流式输出，normal + deep 双模式"""
import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.deps import get_db, require_app_user
from app.models.schemas import ChatRequest, ChatMessage
from app.observability import get_or_create_request_id, log_event, new_turn_id
from app.stream_utils import stream_with_idle_timeout
from db.models import ChatHistory, Session as SessionModel, User
from memory.short_term import (
    get_redis, append_message, increment_round, renew_ttl,
    maybe_compress, cleanup_failed_turn, restore_from_mysql_if_needed,
)
from memory.episodic import record_event
from memory.retrieve import retrieve_context
from rag.llm import get_llm
from tools.web_search import web_search
from tools.calculator import calculator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


# ---- 格式化辅助 ----

def _format_docs(docs: list[dict]) -> str:
    if not docs:
        return "（无相关文档）"
    parts = []
    for d in docs:
        source = d.get("metadata", {}).get("source", "")
        parts.append(f"[来源: {source}]\n{d['content']}")
    return "\n\n".join(parts)


def _format_notes(notes: list[dict]) -> str:
    if not notes:
        return "无"
    parts = []
    for n in notes:
        meta = n.get("metadata", {})
        concept = meta.get("concept", "")
        parts.append(f"[{concept}] {n['content']}")
    return "\n".join(parts)


def _format_episodic(events: list[dict]) -> str:
    if not events:
        return "无"
    return "\n".join(e.get("content", "") for e in events)


def _format_short_term(stm: dict) -> str:
    parts = []
    if stm.get("summary"):
        parts.append(f"[早期对话摘要]\n{stm['summary']}")
    if stm.get("messages"):
        msgs = stm["messages"]
        has_summary = bool(stm.get("summary"))
        if has_summary:
            recent = [f"[早期对话之后第{i+1}条] {m['role']}: {m['content'][:300]}" for i, m in enumerate(msgs)]
        else:
            recent = [f"[第{i+1}条] {m['role']}: {m['content'][:300]}" for i, m in enumerate(msgs)]
        parts.append("[对话记录]\n" + "\n".join(recent))
    return "\n\n".join(parts) if parts else "无"


def _format_sources(context: dict) -> list[dict]:
    sources = []
    for d in context.get("documents", []):
        meta = d.get("metadata", {})
        sources.append({"source": meta.get("source", "unknown"), "score": d.get("score", 0)})
    return sources


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_error_message(e: Exception) -> str:
    error_str = str(e).lower()
    if "timeout" in error_str:
        return "请求超时，请重新提问"
    if "connection" in error_str or "auth" in error_str or "401" in error_str:
        return "AI 服务暂时不可用，请稍后重试"
    return "回答生成中断，请重新提问"


import ast


def _extract_search_sources(output: str) -> list[dict]:
    """从 TavilySearchResults 的字符串输出中提取来源。

    web_search 工具返回 str(results)，需要解析回 list 提取 url + title。
    """
    try:
        results = ast.literal_eval(output)
    except (ValueError, SyntaxError):
        return []
    sources = []
    if isinstance(results, list):
        for r in results:
            if isinstance(r, dict) and r.get("url"):
                title = r.get("title", "")
                sources.append({
                    "source": f"{title} - {r['url']}" if title else r["url"],
                    "score": 0,
                })
    return sources


NORMAL_PROMPT = """你是一个知识库问答助手。请根据以下上下文回答用户的问题。今天是 {current_date}。

# 检索到的文档
{documents}

# 用户笔记
{notes}

# 学习历程
{episodic}

# 对话上下文（[第N条] 为整个会话的对话顺序，编号最小的是最早的消息；若有早期对话摘要，则编号从摘要之后开始）
{short_term}

# 用户的问题
{question}

# 回答要求
1. 优先根据文档内容回答，引用时标注来源，格式：根据《来源名》...
2. 如果文档中未找到相关内容，必须明确回答"文档中未找到相关内容"，严禁使用模型自身知识编造答案
3. 如果文档和笔记中有相关内容但不足以完整回答，请基于已有信息回答并指出信息不足的部分
4. 当用户询问对话历史（如"第一个问题""之前聊了什么"）时，请参考「对话上下文」如实回答，不要编造
5. 涉及"今天""最新""最近"等时间词时，以开头的系统日期为准；调用 web_search 时在查询词中带上当前年份
6. 回答前先思考：用户问的是什么？文档中有没有直接相关的内容？应该怎样组织回答？
7. 回答要简洁准确，使用 Markdown 格式（重点加粗、必要时用列表或表格）

# 示例
用户问题：RAG是什么？
文档内容：RAG（检索增强生成）是一种结合检索与生成的技术，通过从外部知识库检索相关文档来增强LLM的回答。
回答：根据《rag_intro.txt》，**RAG（检索增强生成）**是一种将**检索**与**生成**结合的技术。它通过从外部知识库中检索相关文档，将其作为上下文注入到 LLM 的提示中，从而提升回答的准确性和可信度。"""


# ---- 主端点 ----

@router.post("")
async def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    require_app_user(payload.user_id)
    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    session = db.query(SessionModel).filter(
        SessionModel.id == payload.session_id,
        SessionModel.user_id == payload.user_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    r = get_redis()
    request_id = get_or_create_request_id()
    turn_id = new_turn_id()

    async def event_generator():
        user_msg_id = None
        round_incremented = False
        answer_persisted = False
        first_token_logged = False
        stage = "restore"
        turn_started_at = time.perf_counter()

        def turn_event(event: str, *, level: int = logging.INFO, **fields):
            log_event(
                logger,
                event,
                level=level,
                request_id=request_id,
                turn_id=turn_id,
                mode=payload.mode,
                **fields,
            )

        def record_first_token():
            nonlocal first_token_logged
            if first_token_logged:
                return
            first_token_logged = True
            turn_event(
                "turn_first_token",
                duration_ms=round((time.perf_counter() - turn_started_at) * 1000, 2),
            )

        try:
            turn_event("turn_started")
            # 步骤 1: 先恢复当前请求之前的历史，避免把当前消息恢复并追加两次
            restore_from_mysql_if_needed(r, payload.user_id, payload.session_id, db)

            # 步骤 2: 持久化当前 user 消息
            stage = "persist_user"
            user_msg = ChatHistory(
                user_id=payload.user_id,
                session_id=payload.session_id,
                role="user",
                content=payload.message,
                mode=payload.mode,
            )
            db.add(user_msg)
            db.commit()
            user_msg_id = user_msg.id

            # 步骤 3-4: Redis 写入 + 轮数和 TTL
            stage = "cache_user"
            append_message(
                r,
                payload.user_id,
                payload.session_id,
                "user",
                payload.message,
                message_id=user_msg_id,
            )
            increment_round(r, payload.user_id, payload.session_id)
            round_incremented = True
            renew_ttl(r, payload.user_id, payload.session_id)

            # 步骤 5: 并行检索
            stage = "retrieval"
            retrieval_started_at = time.perf_counter()
            context = await retrieve_context(
                user_id=payload.user_id,
                session_id=payload.session_id,
                question=payload.message,
                mode=payload.mode,
            )
            turn_event(
                "turn_retrieval_completed",
                duration_ms=round((time.perf_counter() - retrieval_started_at) * 1000, 2),
                document_count=len(context.get("documents", [])),
                note_count=len(context.get("notes", [])),
                episodic_count=len(context.get("episodic_memory", [])),
            )

            # 初始化 sources（deep 模式不改此值；normal 模式可能覆盖）
            all_sources = _format_sources(context)

            # 步骤 6: LLM 流式生成
            stage = "llm"
            if payload.mode == "deep":
                full_answer = ""
                async for sse_str in _stream_deep(payload, context):
                    if sse_str.get("type") == "token":
                        full_answer += sse_str["content"]
                        record_first_token()
                        yield _sse("token", {"content": sse_str["content"]})
                    elif sse_str.get("type") == "status":
                        yield _sse("status", {"content": sse_str["content"]})
            else:
                full_answer = ""
                agent_sources = []
                async for event in _stream_normal(payload, context):
                    if event["type"] == "token":
                        full_answer += event["content"]
                        record_first_token()
                        yield _sse("token", {"content": event["content"]})
                    elif event["type"] == "status":
                        yield _sse("status", {"content": event["content"]})
                    elif event["type"] == "sources":
                        agent_sources = event["content"]
                # 合并 RAG 文档来源 + web_search 来源
                all_sources = _format_sources(context) + agent_sources

            # 步骤 7: 写 MySQL assistant 消息
            stage = "persist_answer"
            assistant_msg = ChatHistory(
                user_id=payload.user_id,
                session_id=payload.session_id,
                role="assistant",
                content=full_answer,
            )
            if not session.title:
                session.title = payload.message[:15]
            db.add(assistant_msg)
            db.commit()
            answer_persisted = True

            # 步骤 8-11 是可补偿的派生状态；失败不能破坏已持久化的完整问答
            stage = "derived_state"
            _finalize_completed_turn(
                db=db,
                r=r,
                payload=payload,
                session=session,
                assistant_msg_id=assistant_msg.id,
                full_answer=full_answer,
                request_id=request_id,
                turn_id=turn_id,
            )

            turn_event(
                "turn_completed",
                duration_ms=round((time.perf_counter() - turn_started_at) * 1000, 2),
                source_count=len(all_sources),
                output_chars=len(full_answer),
            )

            # sources + done
            yield _sse("sources", {"content": all_sources})
            yield _sse("done", {})

        except asyncio.CancelledError:
            turn_event(
                "turn_cancelled",
                level=logging.WARNING,
                stage=stage,
                duration_ms=round((time.perf_counter() - turn_started_at) * 1000, 2),
                answer_persisted=answer_persisted,
            )
            if not answer_persisted:
                _cleanup(db, r, payload, user_msg_id, round_incremented)
            raise
        except Exception as e:
            turn_event(
                "turn_failed",
                level=logging.ERROR,
                stage=stage,
                error_type=type(e).__name__,
                duration_ms=round((time.perf_counter() - turn_started_at) * 1000, 2),
                answer_persisted=answer_persisted,
            )
            if not answer_persisted:
                _cleanup(db, r, payload, user_msg_id, round_incremented)
            else:
                db.rollback()
            yield _sse("error", {"content": _get_error_message(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _finalize_completed_turn(
    db: Session,
    r,
    payload: ChatRequest,
    session: SessionModel,
    assistant_msg_id: int,
    full_answer: str,
    request_id: str,
    turn_id: str,
):
    """best-effort 更新已完成问答的派生状态。"""
    assistant_cached = False
    try:
        append_message(
            r,
            payload.user_id,
            payload.session_id,
            "assistant",
            full_answer,
            message_id=assistant_msg_id,
        )
        assistant_cached = True
    except Exception as exc:
        _log_derived_failure(request_id, turn_id, payload.mode, "redis_assistant", exc)

    if assistant_cached:
        try:
            maybe_compress(r, payload.user_id, payload.session_id, db_session=db)
        except Exception as exc:
            db.rollback()
            _log_derived_failure(request_id, turn_id, payload.mode, "memory_compression", exc)

    try:
        record_event(
            db,
            user_id=payload.user_id,
            session_id=payload.session_id,
            event_type="qa_completed",
            content=f"用户提问了「{payload.message[:50]}」，基于{'深度研究' if payload.mode == 'deep' else '普通问答'}模式回答",
            question_text=payload.message,
        )
    except Exception as exc:
        db.rollback()
        _log_derived_failure(request_id, turn_id, payload.mode, "episodic_memory", exc)

    try:
        session.last_active = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        _log_derived_failure(request_id, turn_id, payload.mode, "last_active", exc)

    try:
        renew_ttl(r, payload.user_id, payload.session_id)
    except Exception as exc:
        _log_derived_failure(request_id, turn_id, payload.mode, "redis_ttl", exc)


def _log_derived_failure(
    request_id: str,
    turn_id: str,
    mode: str,
    stage: str,
    exc: Exception,
):
    log_event(
        logger,
        "turn_stage_failed",
        level=logging.WARNING,
        request_id=request_id,
        turn_id=turn_id,
        mode=mode,
        stage=stage,
        error_type=type(exc).__name__,
    )


def _cleanup(
    db: Session,
    r,
    payload: ChatRequest,
    user_msg_id: int | None,
    round_incremented: bool,
):
    """清理尚未完成的当前问答，不影响其他请求或既有消息。"""
    if user_msg_id is not None:
        cleanup_failed_turn(
            r,
            payload.user_id,
            payload.session_id,
            message_id=user_msg_id,
            content=payload.message,
            decrement_round_count=round_incremented,
        )

    try:
        db.rollback()
        if user_msg_id is None:
            return
        db.query(ChatHistory).filter(ChatHistory.id == user_msg_id).delete()
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("失败 user 消息从 MySQL 清理失败: %s", exc)


# ---- normal 模式流式 ----

async def _stream_normal(payload: ChatRequest, context: dict):
    """normal 模式：流式输出 LLM token"""
    llm = get_llm(temperature=0.3)

    prompt = NORMAL_PROMPT.format(
        current_date=datetime.now().strftime("%Y-%m-%d"),
        documents=_format_docs(context.get("documents", [])),
        notes=_format_notes(context.get("notes", [])),
        episodic=_format_episodic(context.get("episodic_memory", [])),
        short_term=_format_short_term(context.get("short_term_memory", {})),
        question=payload.message,
    )

    from config import TAVILY_API_KEY

    # 如果有 Tavily key，用 Agent 模式（支持工具调用）
    if TAVILY_API_KEY:
        try:
            async for event in _agent_stream(prompt):
                yield event
            return
        except Exception as e:
            logger.warning(f"Agent 模式失败，fallback 到普通 LLM: {e}")

    # 普通 LLM 流式（带 idle timeout 保护）
    async for chunk in stream_with_idle_timeout(llm.astream(prompt), timeout=30.0):
        if chunk.content:
            yield {"type": "token", "content": chunk.content}


async def _agent_stream(prompt: str):
    """LangChain 1.x create_agent 流式：工具调用 status + LLM token + web_search sources

    yield 字典事件：
      {"type": "status", "content": "..."}  — web_search 开始时
      {"type": "token", "content": "..."}   — LLM 流式 token
      {"type": "sources", "content": [...]} — 最终合并的 web_search 来源
    """
    from langchain.agents import create_agent

    llm = get_llm(temperature=0.3)
    tools = [web_search, calculator]
    today = datetime.now().strftime("%Y-%m-%d")
    system_prompt = (
        f"你是一个知识库问答助手。今天是 {today}。\n"
        "请根据提供的上下文回答问题，遵循以下要求：\n"
        "1. 优先根据文档内容回答，引用时标注来源，格式：根据《来源名》...\n"
        "2. 如果需要最新信息可以使用 web_search（搜索词请带当前年份），需要计算可以用计算器\n"
        "3. 文档中未找到相关内容时，必须明确说明，严禁使用模型自身知识编造答案\n"
        "4. 当用户询问对话历史时，请参考上下文中的「对话上下文」如实回答\n"
        "5. 回答前先思考：用户问的是什么？文档中有没有直接相关的内容？\n"
        "6. 回答使用 Markdown 格式，简洁准确"
    )
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

    collected_sources = []

    async for event in agent.astream_events({"messages": [("user", prompt)]}, version="v2"):
        etype = event["event"]

        # 工具开始：仅 web_search 发 status（有网络延迟，用户可感知）
        if etype == "on_tool_start" and event["name"] == "web_search":
            tool_input = event["data"].get("input")
            query = ""
            if isinstance(tool_input, dict):
                query = tool_input.get("query", "")
            elif isinstance(tool_input, str):
                query = tool_input
            if query:
                yield {"type": "status", "content": f"正在搜索：{query}"}

        # 工具结束：仅 web_search 收集来源（calculator 无来源语义）
        elif etype == "on_tool_end" and event["name"] == "web_search":
            output = event["data"].get("output", "")
            if isinstance(output, str):
                collected_sources.extend(_extract_search_sources(output))

        # LLM 流式 token
        elif etype == "on_chat_model_stream":
            token = event["data"]["chunk"].content
            if token:
                yield {"type": "token", "content": token}

    # 最终合并来源
    if collected_sources:
        yield {"type": "sources", "content": collected_sources}


# ---- deep 模式流式 ----

async def _stream_deep(payload: ChatRequest, context: dict):
    """deep 模式：LangGraph 多 Agent，产出 status + token 事件"""
    from agents.graph import get_graph

    initial_state = {
        "original_question": payload.message,
        "sub_questions": [],
        "retrieved_docs": [],
        "notes": context.get("notes", []),
        "episodic_memory": context.get("episodic_memory", []),
        "short_term_memory": _format_short_term(context.get("short_term_memory", {})),
        "final_answer": "",
        "user_id": payload.user_id,
    }

    graph = get_graph()

    # 初始 status
    yield {"type": "status", "content": "正在拆解问题..."}

    async for mode_name, data in graph.astream(initial_state, stream_mode=["updates", "custom"]):
        if mode_name == "updates":
            for node_name, update in data.items():
                if "sub_questions" in update:
                    sqs = update["sub_questions"]
                    yield {"type": "status", "content": (
                        f"已拆解为 {len(sqs)} 个子问题:\n" +
                        "\n".join(f"- {q}" for q in sqs)
                    )}
                    yield {"type": "status", "content": "正在检索文档..."}
                elif "retrieved_docs" in update:
                    docs = update["retrieved_docs"]
                    yield {"type": "status", "content": f"已检索到 {len(docs)} 段相关内容"}
                    yield {"type": "status", "content": "正在生成回答..."}
        elif mode_name == "custom":
            if isinstance(data, dict) and data.get("type") == "token":
                yield {"type": "token", "content": data["content"]}


@router.get("/history", response_model=list[ChatMessage])
def get_chat_history(
    user_id: str = Query(...),
    session_id: str = Query(...),
    db: Session = Depends(get_db),
):
    require_app_user(user_id)
    session = db.query(SessionModel).filter(
        SessionModel.id == session_id,
        SessionModel.user_id == user_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    return db.query(ChatHistory).filter(
        ChatHistory.user_id == user_id,
        ChatHistory.session_id == session_id,
    ).order_by(ChatHistory.created_at.asc()).all()
