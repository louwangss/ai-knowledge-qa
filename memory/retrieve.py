"""并行检索：所有记忆源并行查询，合并返回"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from db.database import SessionLocal
from memory.conversation import load_conversation_memory
from memory.episodic import get_recent_events

if TYPE_CHECKING:
    from langchain_chroma import Chroma

logger = logging.getLogger(__name__)


def get_rag_vector_store():
    """首次检索时再加载 Chroma 与 embedding 依赖。"""
    from rag.vector_store import get_rag_vector_store as get_store

    return get_store()


def get_semantic_vector_store():
    """首次检索时再加载语义记忆向量库。"""
    from rag.vector_store import get_semantic_vector_store as get_store

    return get_store()


def _sync_chroma_search(
    vector_store: Chroma,
    user_id: str,
    question: str,
    top_k: int,
    doc_type: str,
) -> list[dict]:
    """同步 Chroma 检索（用 to_thread 包装）"""
    results = vector_store.similarity_search_with_relevance_scores(
        question,
        k=top_k,
        filter={"$and": [{"user_id": user_id}, {"type": doc_type}]},
    )
    return [
        {
            "content": doc.page_content,
            "metadata": doc.metadata,
            "score": score,
        }
        for doc, score in results
    ]


def _filter_by_relevance(docs: list[dict], threshold: float) -> list[dict]:
    """过滤掉相关度低于阈值的文档"""
    return [d for d in docs if d.get("score", 0) >= threshold]


def _load_recent_events(user_id: str, limit: int):
    """在当前工作线程内创建并关闭情景记忆查询 Session。"""
    db = SessionLocal()
    try:
        return get_recent_events(db, user_id, limit)
    finally:
        db.close()


def _load_conversation(user_id: str, session_id: str) -> dict:
    """在当前工作线程内读取并关闭 MySQL 权威会话记忆 Session。"""
    db = SessionLocal()
    try:
        return load_conversation_memory(db, user_id, session_id)
    finally:
        db.close()


async def retrieve_context(
    user_id: str,
    session_id: str,
    question: str,
    mode: str = "normal",
) -> dict:
    """并行检索所有记忆源，合并返回。

    Args:
        mode: "normal" 执行全部 4 路；"deep" 跳过文档检索（Agent B 会做）
    """
    rag_vs = get_rag_vector_store()
    semantic_vs = get_semantic_vector_store()

    # Chroma 是同步 CPU 密集操作，用 to_thread 包装
    async def search_async(vs, uid, q, top_k, doc_type):
        return await asyncio.to_thread(_sync_chroma_search, vs, uid, q, top_k, doc_type)

    async def search_episodic(uid, limit):
        return await asyncio.to_thread(_load_recent_events, uid, limit)

    async def get_conversation(uid, sid):
        return await asyncio.to_thread(_load_conversation, uid, sid)

    if mode == "normal":
        doc_results, note_results, episodic_results, short_term = await asyncio.gather(
            search_async(rag_vs, user_id, question, top_k=3, doc_type="document"),
            search_async(semantic_vs, user_id, question, top_k=3, doc_type="note"),
            search_episodic(user_id, limit=5),
            get_conversation(user_id, session_id),
        )
    else:  # deep 模式跳过文档检索
        note_results, episodic_results, short_term = await asyncio.gather(
            search_async(semantic_vs, user_id, question, top_k=3, doc_type="note"),
            search_episodic(user_id, limit=5),
            get_conversation(user_id, session_id),
        )
        doc_results = []

    from config import RAG_RELEVANCE_THRESHOLD

    # 过滤低相关度文档
    doc_results = _filter_by_relevance(doc_results, RAG_RELEVANCE_THRESHOLD)

    return {
        "documents": doc_results,
        "notes": note_results,
        "episodic_memory": [
            {"content": e.content, "event_type": e.event_type}
            for e in episodic_results
        ],
        "short_term_memory": short_term,
    }
