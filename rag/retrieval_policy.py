"""normal/deep 共用的检索过滤、权威版本校验与去重策略。"""

from collections.abc import Callable

from sqlalchemy import select

from config import RAG_RELEVANCE_THRESHOLD
from db.models import Document, SemanticMemory


def _metadata_version(metadata: dict) -> int | None:
    try:
        return int(metadata["index_version"])
    except (KeyError, TypeError, ValueError):
        return None


def filter_authoritative_results(
    candidates: list[dict],
    *,
    user_id: str,
    doc_type: str,
    session_factory: Callable | None = None,
    threshold: float = RAG_RELEVANCE_THRESHOLD,
) -> list[dict]:
    """只保留与 MySQL 当前 ready 版本一致的向量结果，并按稳定身份去重。"""
    eligible = [item for item in candidates if item.get("score", 0) >= threshold]
    raw_ids = {
        str(item.get("metadata", {}).get("mysql_id"))
        for item in eligible
        if item.get("metadata", {}).get("mysql_id") is not None
    }
    if not raw_ids:
        return []

    if session_factory is None:
        from db.database import SessionLocal

        session_factory = SessionLocal

    db = session_factory()
    try:
        if doc_type == "document":
            entities = db.scalars(select(Document).where(
                Document.user_id == user_id,
                Document.id.in_(raw_ids),
            )).all()
        elif doc_type == "note":
            note_ids = [int(value) for value in raw_ids if value.isdigit()]
            entities = db.scalars(select(SemanticMemory).where(
                SemanticMemory.user_id == user_id,
                SemanticMemory.id.in_(note_ids),
            )).all()
        else:
            raise ValueError(f"不支持的检索类型: {doc_type}")
    finally:
        db.close()

    by_id = {str(entity.id): entity for entity in entities}
    accepted: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in eligible:
        metadata = item.get("metadata", {})
        entity_id = str(metadata.get("mysql_id", ""))
        entity = by_id.get(entity_id)
        vector_version = _metadata_version(metadata)
        if entity is None or vector_version is None:
            continue
        if doc_type == "document":
            is_current = (
                entity.status == "ready"
                and entity.index_version == entity.indexed_version == vector_version
            )
        else:
            is_current = (
                entity.index_state == "ready"
                and entity.index_version == entity.indexed_version == vector_version
            )
        if not is_current:
            continue

        chunk_identity = str(metadata.get("chunk_index", "single"))
        identity = (entity_id, chunk_identity)
        if identity in seen:
            continue
        seen.add(identity)
        accepted.append(item)
    return accepted


def search_indexed_content(
    vector_store,
    *,
    user_id: str,
    question: str,
    top_k: int,
    doc_type: str,
    session_factory: Callable | None = None,
) -> list[dict]:
    """执行向量检索并应用统一可信度策略。"""
    results = vector_store.similarity_search_with_relevance_scores(
        question,
        k=top_k,
        filter={"$and": [{"user_id": user_id}, {"type": doc_type}]},
    )
    candidates = [
        {"content": doc.page_content, "metadata": doc.metadata, "score": score}
        for doc, score in results
    ]
    return filter_authoritative_results(
        candidates,
        user_id=user_id,
        doc_type=doc_type,
        session_factory=session_factory,
    )
