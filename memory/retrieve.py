"""并行检索：所有记忆源并行查询，合并返回"""
import asyncio
import logging

from langchain_chroma import Chroma
from sqlalchemy.orm import Session

from rag.vector_store import get_rag_vector_store, get_semantic_vector_store
from memory.episodic import get_recent_events
from memory.short_term import get_short_term_memory, get_redis

logger = logging.getLogger(__name__)


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


async def retrieve_context(
    user_id: str,
    session_id: str,
    question: str,
    db: Session,
    mode: str = "normal",
) -> dict:
    """并行检索所有记忆源，合并返回。

    Args:
        mode: "normal" 执行全部 4 路；"deep" 跳过文档检索（Agent B 会做）
    """
    r = get_redis()
    rag_vs = get_rag_vector_store()
    semantic_vs = get_semantic_vector_store()

    # Chroma 是同步 CPU 密集操作，用 to_thread 包装
    async def search_async(vs, uid, q, top_k, doc_type):
        return await asyncio.to_thread(_sync_chroma_search, vs, uid, q, top_k, doc_type)

    async def search_episodic(db, uid, limit):
        return await asyncio.to_thread(get_recent_events, db, uid, limit)

    async def get_short_term(r, uid, sid, db):
        return await asyncio.to_thread(get_short_term_memory, r, uid, sid, db)

    if mode == "normal":
        doc_results, note_results, episodic_results, short_term = await asyncio.gather(
            search_async(rag_vs, user_id, question, top_k=3, doc_type="document"),
            search_async(semantic_vs, user_id, question, top_k=3, doc_type="note"),
            search_episodic(db, user_id, limit=5),
            get_short_term(r, user_id, session_id, db),
        )
    else:  # deep 模式跳过文档检索
        note_results, episodic_results, short_term = await asyncio.gather(
            search_async(semantic_vs, user_id, question, top_k=3, doc_type="note"),
            search_episodic(db, user_id, limit=5),
            get_short_term(r, user_id, session_id, db),
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
