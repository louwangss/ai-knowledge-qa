"""语义记忆：MySQL source of truth + Chroma 索引层"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import IndexJob, SemanticMemory
from app.note_version import build_note_version
from indexing.jobs import enqueue_index_job

logger = logging.getLogger(__name__)

_SIMILARITY_DISTANCE_THRESHOLD = 0.15  # 余弦距离 <= 此值认为重复


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
        index_version=1,
        indexed_version=1 if not content.strip() else 0,
        index_state="ready" if not content.strip() else "pending",
    )
    db.add(note)
    db.flush()
    if content.strip():
        enqueue_index_job(db, "note", str(note.id), "upsert", note.index_version)
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
    note_query = db.query(SemanticMemory).filter(
        SemanticMemory.id == note_id,
        SemanticMemory.user_id == user_id,
    )
    if expected_version is not None:
        # MySQL 行锁让同一笔记的版本检查与写入成为一个原子临界区。
        note_query = note_query.with_for_update()
    note = note_query.first()
    if not note:
        return None

    if expected_version is not None:
        current_version = build_note_version(note.concept, note.content)
        if current_version != expected_version:
            db.rollback()
            raise NoteVersionConflictError

    if concept is not None:
        note.concept = concept
    if content is not None:
        note.content = content
    note.updated_at = datetime.utcnow()
    note.index_version = (note.index_version or 0) + 1
    note.index_state = "pending"
    enqueue_index_job(db, "note", str(note.id), "upsert", note.index_version)
    db.commit()
    db.refresh(note)
    return note


def delete_note(db: Session, note_id: int, user_id: str) -> bool:
    """原子标记删除并写入持久化任务，不在请求事务中访问 Chroma。"""
    note = db.query(SemanticMemory).filter(
        SemanticMemory.id == note_id,
        SemanticMemory.user_id == user_id,
        SemanticMemory.index_state != "deleting",
    ).first()
    if not note:
        return False

    note.index_version = (note.index_version or 0) + 1
    note.index_state = "deleting"
    enqueue_index_job(db, "note", str(note.id), "delete", note.index_version)
    db.commit()
    return True


def get_notes(db: Session, user_id: str) -> list[SemanticMemory]:
    """获取用户所有笔记"""
    return db.query(SemanticMemory).filter(
        SemanticMemory.user_id == user_id,
        SemanticMemory.index_state != "deleting",
    ).order_by(SemanticMemory.created_at.desc()).all()


def get_note_summaries(db: Session, user_id: str):
    """只读取列表展示所需字段，避免传输全部笔记正文。"""
    return db.query(
        SemanticMemory.id,
        SemanticMemory.concept,
        SemanticMemory.updated_at,
    ).filter(
        SemanticMemory.user_id == user_id,
        SemanticMemory.index_state != "deleting",
    ).order_by(SemanticMemory.created_at.desc()).all()


def get_note(db: Session, note_id: int, user_id: str) -> SemanticMemory | None:
    """按用户边界读取单篇笔记。"""
    return db.query(SemanticMemory).filter(
        SemanticMemory.id == note_id,
        SemanticMemory.user_id == user_id,
        SemanticMemory.index_state != "deleting",
    ).first()


def _sync_to_chroma(db: Session, note: SemanticMemory, index_version: int):
    """用稳定 ID 覆盖当前向量，并清理旧版或失败遗留的向量。"""
    vs = get_semantic_vector_store()
    existing = vs.get(where={"mysql_id": str(note.id)})
    existing_ids = list(existing.get("ids", [])) if existing else []

    if not note.content.strip():
        if existing_ids:
            vs.delete(ids=existing_ids)
        note.chroma_id = ""
        note.indexed_version = index_version
        note.index_state = "ready"
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
            "index_version": index_version,
        }],
        ids=[stable_id],
    )
    legacy_ids = [vector_id for vector_id in existing_ids if vector_id != stable_id]
    if legacy_ids:
        vs.delete(ids=legacy_ids)
    note.chroma_id = stable_id
    note.indexed_version = index_version
    note.index_state = "ready"


def _delete_note_vectors(note_id: int) -> None:
    """删除同一 MySQL 笔记对应的所有 Chroma 向量。"""
    from rag.vector_store import delete_semantic_vectors_by_mysql_id

    delete_semantic_vectors_by_mysql_id(str(note_id))


def process_note_index_job(db: Session, job: IndexJob) -> None:
    """执行持久化笔记任务；仅当前期望版本可以改变实体状态。"""
    note = db.get(SemanticMemory, int(job.entity_id))
    if note is None or note.index_version != job.desired_version:
        return
    if job.operation == "delete":
        _delete_note_vectors(note.id)
        db.delete(note)
        return
    if job.operation != "upsert":
        raise ValueError(f"不支持的笔记索引操作: {job.operation}")

    note.index_state = "indexing"
    db.commit()
    note = db.get(SemanticMemory, int(job.entity_id))
    if note is not None and note.index_version == job.desired_version:
        _sync_to_chroma(db, note, job.desired_version)


def sync_note_index_task(note_id: int, user_id: str) -> None:
    """兼容 FastAPI 后台触发点：执行该笔记最新的未完成持久化任务。"""
    from db.database import SessionLocal
    from indexing.jobs import run_index_job

    db = SessionLocal()
    try:
        job_id = db.scalar(
            select(IndexJob.id)
            .where(
                IndexJob.entity_type == "note",
                IndexJob.entity_id == str(note_id),
                IndexJob.status.in_(("pending", "failed")),
            )
            .order_by(IndexJob.desired_version.desc())
            .limit(1)
        )
        if job_id is not None:
            run_index_job(db, job_id)
    finally:
        db.close()


def compensation_task(db: Session):
    """兼容旧调用方；实际补偿统一由持久化任务扫描器完成。"""
    from indexing.jobs import process_pending_index_jobs

    return process_pending_index_jobs()
