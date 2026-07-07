"""Agent B：对每个子问题检索 Chroma 文档"""
import logging
from concurrent.futures import ThreadPoolExecutor

from agents.state import ResearchState, RetrievedDoc
from rag.vector_store import get_rag_vector_store

logger = logging.getLogger(__name__)


def _sync_search(question: str, user_id: str, top_k: int = 3) -> list[RetrievedDoc]:
    """同步检索单个子问题"""
    vs = get_rag_vector_store()
    results = vs.similarity_search_with_relevance_scores(
        question,
        k=top_k,
        filter={"$and": [{"user_id": user_id}, {"type": "document"}]},
    )
    docs: list[RetrievedDoc] = []
    for doc, score in results:
        docs.append(RetrievedDoc(
            content=doc.page_content,
            source=doc.metadata.get("source", "unknown"),
            sub_question=question,
            score=score,
        ))
    return docs


def agent_b_retrieve(state: ResearchState) -> dict:
    """对每个子问题检索，结果去重"""
    sub_questions = state.get("sub_questions", [])

    user_id = state.get("user_id", "")
    if not user_id:
        logger.error("Agent B: 缺少 user_id")
        return {"retrieved_docs": []}

    # 用线程池并行检索，避免 asyncio 事件循环冲突
    with ThreadPoolExecutor(max_workers=min(5, len(sub_questions) or 1)) as pool:
        all_results = list(pool.map(
            lambda q: _sync_search(q, user_id),
            sub_questions,
        ))

    # 合并并去重（按 content 去重，同一段文档对多个子问题相关时只保留一次）
    seen_content = set()
    deduped: list[RetrievedDoc] = []
    for docs in all_results:
        for doc in docs:
            if doc["content"] not in seen_content:
                seen_content.add(doc["content"])
                deduped.append(doc)

    return {"retrieved_docs": deduped}
