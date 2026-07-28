"""语义记忆：MySQL source of truth + Chroma 索引层"""
import logging
from datetime import datetime
from collections import defaultdict
from threading import Lock

from sqlalchemy.orm import Session

from db.models import SemanticMemory
from app.note_version import build_note_version

logger = logging.getLogger(__name__)

_SIMILARITY_DISTANCE_THRESHOLD = 0.15  # 余弦距离 <= 此值认为重复
_NOTE_SYNC_LOCKS: defaultdict[int, Lock] = defaultdict(Lock)


class NoteVersionConflictError(Exception):
    """客户端基于过期内容尝试保存。"""


def get_semantic_vector_store():
    """首次处理笔记向量时再加载 Chroma 与 embedding 依赖。"""
    from rag.vector_store import get_semantic_vector_store as get_store

    return get_store()


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
) -> SemanticMemory:
    """只在 MySQL 创建笔记；向量索引由后台任务异步生成。"""
    note = SemanticMemory(
        user_id=user_id,
        concept=concept,
        content=content,
        # 空字符串表示空白草稿无需向量，NULL 表示等待后台同步。
        chroma_id="" if not content.strip() else None,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def update_note(
    db: Session,
    note_id: int,
    user_id: str,
    concept: str | None,
    content: str | None,
    expected_version: str | None = None,
) -> SemanticMemory | None:
    """编辑笔记（不做相似度检测）"""
    note = db.query(SemanticMemory).filter(
        SemanticMemory.id == note_id,
        SemanticMemory.user_id == user_id,
    ).first()
    if not note:
        return None

    if expected_version is not None:
        current_version = build_note_version(note.concept, note.content)
        if current_version != expected_version:
            raise NoteVersionConflictError

        query = db.query(SemanticMemory).filter(
            SemanticMemory.id == note_id,
            SemanticMemory.user_id == user_id,
            SemanticMemory.content == note.content,
        )
        query = query.filter(
            SemanticMemory.concept.is_(None)
            if note.concept is None
            else SemanticMemory.concept == note.concept
        )
        changed = query.update(
            {
                SemanticMemory.concept: note.concept if concept is None else concept,
                SemanticMemory.content: note.content if content is None else content,
                SemanticMemory.updated_at: datetime.utcnow(),
                SemanticMemory.chroma_id: None,
            },
            synchronize_session=False,
        )
        if changed != 1:
            db.rollback()
            raise NoteVersionConflictError
        db.commit()
        return db.query(SemanticMemory).filter(
            SemanticMemory.id == note_id,
            SemanticMemory.user_id == user_id,
        ).first()

    if concept is not None:
        note.concept = concept
    if content is not None:
        note.content = content
    note.updated_at = datetime.utcnow()
    # MySQL 是权威数据源；NULL 标记后台需要重建派生向量。
    note.chroma_id = None
    db.commit()
    db.refresh(note)
    return note


def delete_note(db: Session, note_id: int, user_id: str) -> bool:
    """同步清理派生向量后删除 MySQL 权威记录。"""
    with _NOTE_SYNC_LOCKS[note_id]:
        note = db.query(SemanticMemory).filter(
            SemanticMemory.id == note_id,
            SemanticMemory.user_id == user_id,
        ).first()
        if not note:
            return False

        # 清理失败时保留 MySQL 记录，避免留下仍可被检索的孤立向量。
        _delete_note_vectors(note.id)

        db.delete(note)
        db.commit()
    return True


def get_notes(db: Session, user_id: str) -> list[SemanticMemory]:
    """获取用户所有笔记"""
    return db.query(SemanticMemory).filter(
        SemanticMemory.user_id == user_id,
    ).order_by(SemanticMemory.created_at.desc()).all()


def get_note_summaries(db: Session, user_id: str):
    """只读取列表展示所需字段，避免传输全部笔记正文。"""
    return db.query(
        SemanticMemory.id,
        SemanticMemory.concept,
        SemanticMemory.updated_at,
    ).filter(
        SemanticMemory.user_id == user_id,
    ).order_by(SemanticMemory.created_at.desc()).all()


def get_note(db: Session, note_id: int, user_id: str) -> SemanticMemory | None:
    """按用户边界读取单篇笔记。"""
    return db.query(SemanticMemory).filter(
        SemanticMemory.id == note_id,
        SemanticMemory.user_id == user_id,
    ).first()


def _sync_to_chroma(db: Session, note: SemanticMemory):
    """用稳定 ID 覆盖当前向量，并清理旧版或失败遗留的向量。"""
    vs = get_semantic_vector_store()
    existing = vs.get(where={"mysql_id": str(note.id)})
    existing_ids = list(existing.get("ids", [])) if existing else []

    if not note.content.strip():
        if existing_ids:
            vs.delete(ids=existing_ids)
        note.chroma_id = ""
        db.commit()
        return

    stable_id = f"semantic-note-{note.id}"
    vs.add_texts(
        texts=[note.content],
        metadatas=[{
            "user_id": note.user_id,
            "mysql_id": str(note.id),
            "type": "note",
            "concept": note.concept or "",
            "created_at": note.created_at.isoformat() if note.created_at else "",
            "updated_at": note.updated_at.isoformat() if note.updated_at else "",
        }],
        ids=[stable_id],
    )
    legacy_ids = [vector_id for vector_id in existing_ids if vector_id != stable_id]
    if legacy_ids:
        vs.delete(ids=legacy_ids)
    note.chroma_id = stable_id
    db.commit()


def _delete_note_vectors(note_id: int) -> None:
    """删除同一 MySQL 笔记对应的所有 Chroma 向量。"""
    from rag.vector_store import delete_semantic_vectors_by_mysql_id

    delete_semantic_vectors_by_mysql_id(str(note_id))


def sync_note_index_task(note_id: int, user_id: str) -> None:
    """后台按笔记串行读取最新 MySQL 内容并同步派生向量。"""
    from db.database import SessionLocal

    with _NOTE_SYNC_LOCKS[note_id]:
        db = SessionLocal()
        try:
            note = db.query(SemanticMemory).filter(
                SemanticMemory.id == note_id,
                SemanticMemory.user_id == user_id,
            ).first()
            if note is not None:
                _sync_to_chroma(db, note)
        except Exception as e:
            db.rollback()
            logger.error(
                "笔记向量后台同步失败: note_id=%s error_type=%s",
                note_id,
                type(e).__name__,
            )
        finally:
            db.close()


def compensation_task(db: Session):
    """后台补偿：扫描 chroma_id IS NULL 的笔记，补写到 Chroma"""
    notes = db.query(SemanticMemory).filter(
        SemanticMemory.chroma_id.is_(None),
    ).all()
    synced_count = 0
    for note in notes:
        try:
            _sync_to_chroma(db, note)
            synced_count += 1
        except Exception as e:
            db.rollback()
            logger.warning(
                "补偿单条笔记失败: note_id=%s error_type=%s",
                note.id,
                type(e).__name__,
            )
    if notes:
        logger.info("补偿任务完成: 成功同步 %s/%s 条笔记", synced_count, len(notes))
