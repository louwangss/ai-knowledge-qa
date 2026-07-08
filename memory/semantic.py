"""语义记忆：MySQL source of truth + Chroma 索引层"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from db.models import SemanticMemory
from rag.vector_store import get_semantic_vector_store, get_embeddings

logger = logging.getLogger(__name__)

_SIMILARITY_DISTANCE_THRESHOLD = 0.15  # 余弦距离 <= 此值认为重复


def check_similarity(db: Session, user_id: str, content: str) -> SemanticMemory | None:
    """检查是否有相似笔记。

    Returns:
        如果找到距离 <= 阈值的笔记，返回该笔记；否则返回 None。
    """
    vs = get_semantic_vector_store()

    # 用原始余弦距离判断（距离越小越相似）
    results = vs.similarity_search_with_score(
        content,
        k=1,
        filter={"$and": [{"user_id": user_id}, {"type": "note"}]},
    )
    if not results:
        return None

    doc, distance = results[0]
    if distance <= _SIMILARITY_DISTANCE_THRESHOLD:
        logger.info(f"相似笔记命中: distance={distance:.4f}")
        # 通过 mysql_id 回查 MySQL（复用调用方的 session）
        mysql_id = doc.metadata.get("mysql_id")
        if mysql_id:
            note = db.query(SemanticMemory).filter(SemanticMemory.id == int(mysql_id)).first()
            return note
    return None


def create_note(
    db: Session,
    user_id: str,
    concept: str,
    content: str,
) -> tuple[SemanticMemory, bool]:
    """创建笔记。

    Returns:
        (note, is_duplicate) — is_duplicate=True 表示跳过写入（相似度检测命中）
    """
    # 1. 相似度检测
    existing = check_similarity(db, user_id, content)
    if existing:
        return existing, True

    # 2. MySQL INSERT（chroma_id = NULL）
    note = SemanticMemory(
        user_id=user_id,
        concept=concept,
        content=content,
        chroma_id=None,
    )
    db.add(note)
    db.commit()
    db.refresh(note)

    # 3. Chroma ADD
    _sync_to_chroma(db, note)

    return note, False


def update_note(
    db: Session,
    note_id: int,
    user_id: str,
    concept: str | None,
    content: str | None,
) -> SemanticMemory | None:
    """编辑笔记（不做相似度检测）"""
    note = db.query(SemanticMemory).filter(
        SemanticMemory.id == note_id,
        SemanticMemory.user_id == user_id,
    ).first()
    if not note:
        return None

    if concept is not None:
        note.concept = concept
    if content is not None:
        note.content = content
    note.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)

    # 删除旧向量，重新写入
    if note.chroma_id:
        try:
            vs = get_semantic_vector_store()
            vs.delete(ids=[note.chroma_id])
        except Exception as e:
            logger.warning(f"删除旧向量失败: {e}")

    note.chroma_id = None
    db.commit()
    _sync_to_chroma(db, note)

    return note


def delete_note(db: Session, note_id: int, user_id: str) -> bool:
    """删除笔记"""
    note = db.query(SemanticMemory).filter(
        SemanticMemory.id == note_id,
        SemanticMemory.user_id == user_id,
    ).first()
    if not note:
        return False

    # 先删 Chroma
    if note.chroma_id:
        try:
            vs = get_semantic_vector_store()
            vs.delete(ids=[note.chroma_id])
        except Exception as e:
            logger.warning(f"删除向量失败: {e}")

    db.delete(note)
    db.commit()
    return True


def get_notes(db: Session, user_id: str) -> list[SemanticMemory]:
    """获取用户所有笔记"""
    return db.query(SemanticMemory).filter(
        SemanticMemory.user_id == user_id,
    ).order_by(SemanticMemory.created_at.desc()).all()


def _sync_to_chroma(db: Session, note: SemanticMemory):
    """将笔记同步到 Chroma"""
    vs = get_semantic_vector_store()
    chroma_id = vs.add_texts(
        texts=[note.content],
        metadatas=[{
            "user_id": note.user_id,
            "mysql_id": str(note.id),
            "type": "note",
            "concept": note.concept or "",
            "created_at": note.created_at.isoformat() if note.created_at else "",
        }],
    )
    # langchain-chroma add_texts 返回 id 列表
    if isinstance(chroma_id, list) and chroma_id:
        note.chroma_id = chroma_id[0]
        db.commit()
    elif isinstance(chroma_id, str):
        note.chroma_id = chroma_id
        db.commit()


def compensation_task(db: Session):
    """后台补偿：扫描 chroma_id IS NULL 的笔记，补写到 Chroma"""
    notes = db.query(SemanticMemory).filter(
        SemanticMemory.chroma_id.is_(None),
    ).all()
    for note in notes:
        # 先检查 Chroma 是否已有该 mysql_id 的向量
        vs = get_semantic_vector_store()
        existing = vs.get(where={"mysql_id": str(note.id)})
        if existing and existing.get("ids"):
            # 已有，直接更新 chroma_id
            note.chroma_id = existing["ids"][0]
            db.commit()
        else:
            # 没有，写入
            _sync_to_chroma(db, note)
    if notes:
        logger.info(f"补偿任务完成: 补写了 {len(notes)} 条笔记到 Chroma")
