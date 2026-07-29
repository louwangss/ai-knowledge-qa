"""文档与笔记共用的持久化索引任务状态机。"""

import logging
import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from config import (
    INDEX_JOB_BATCH_SIZE,
    INDEX_JOB_LEASE_SECONDS,
    INDEX_JOB_MAX_ATTEMPTS,
    INDEX_JOB_RETRY_BASE_SECONDS,
    INDEX_JOB_RETRY_MAX_SECONDS,
)
from db.models import Document, IndexJob, SemanticMemory


logger = logging.getLogger(__name__)


def enqueue_index_job(
    db: Session,
    entity_type: str,
    entity_id: str,
    operation: str,
    desired_version: int,
) -> IndexJob:
    """在调用方事务中幂等创建任务；调用方负责提交源记录与任务。"""
    existing = db.scalar(select(IndexJob).where(
        IndexJob.entity_type == entity_type,
        IndexJob.entity_id == str(entity_id),
        IndexJob.operation == operation,
        IndexJob.desired_version == desired_version,
    ))
    if existing is not None:
        if existing.status in {"failed", "exhausted"}:
            existing.status = "pending"
            existing.next_attempt_at = datetime.utcnow()
            existing.last_error_type = None
            if existing.attempt_count >= INDEX_JOB_MAX_ATTEMPTS:
                existing.attempt_count = 0
        return existing

    job = IndexJob(
        entity_type=entity_type,
        entity_id=str(entity_id),
        operation=operation,
        desired_version=desired_version,
        status="pending",
        next_attempt_at=datetime.utcnow(),
    )
    db.add(job)
    db.flush()
    return job


def _retry_delay_seconds(attempt_count: int) -> float:
    exponent = max(0, min(attempt_count - 1, 16))
    backoff = INDEX_JOB_RETRY_BASE_SECONDS * (2 ** exponent)
    jittered = backoff + random.uniform(0, INDEX_JOB_RETRY_BASE_SECONDS)
    return min(INDEX_JOB_RETRY_MAX_SECONDS, jittered)


def _claim_job(db: Session, job_id: int) -> tuple[IndexJob, str] | None:
    now = datetime.utcnow()
    job = db.scalar(
        select(IndexJob)
        .where(
            IndexJob.id == job_id,
            IndexJob.status.in_(("pending", "failed", "running")),
            IndexJob.next_attempt_at <= now,
            or_(IndexJob.lease_expires_at.is_(None), IndexJob.lease_expires_at <= now),
        )
        .with_for_update()
    )
    if job is None:
        db.rollback()
        return None

    owner = str(uuid.uuid4())
    job.status = "running"
    job.lease_owner = owner
    job.lease_expires_at = now + timedelta(seconds=INDEX_JOB_LEASE_SECONDS)
    db.commit()
    return job, owner


def _dispatch(db: Session, job: IndexJob) -> None:
    if job.entity_type == "document":
        from app.api.routes_documents import process_document_index_job

        process_document_index_job(db, job)
        return
    if job.entity_type == "note":
        from memory.semantic import process_note_index_job

        process_note_index_job(db, job)
        return
    raise ValueError(f"不支持的索引实体类型: {job.entity_type}")


def _mark_entity_failed(db: Session, job: IndexJob) -> None:
    if job.operation != "upsert":
        return
    if job.entity_type == "document":
        entity = db.get(Document, job.entity_id)
        if entity is not None and entity.index_version == job.desired_version:
            entity.status = "failed"
    elif job.entity_type == "note":
        entity = db.get(SemanticMemory, int(job.entity_id))
        if entity is not None and entity.index_version == job.desired_version:
            entity.index_state = "failed"


def run_index_job(db: Session, job_id: int) -> bool:
    """尝试执行单个到期任务；返回是否成功完成。"""
    claimed = _claim_job(db, job_id)
    if claimed is None:
        return False
    job, owner = claimed

    try:
        _dispatch(db, job)
        current = db.scalar(select(IndexJob).where(
            IndexJob.id == job.id,
            IndexJob.lease_owner == owner,
            IndexJob.status == "running",
        ))
        if current is None:
            db.rollback()
            return False
        current.status = "completed"
        current.lease_owner = None
        current.lease_expires_at = None
        current.last_error_type = None
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        current = db.get(IndexJob, job_id)
        if current is not None and current.lease_owner == owner:
            current.attempt_count += 1
            current.status = (
                "exhausted"
                if current.attempt_count >= INDEX_JOB_MAX_ATTEMPTS
                else "failed"
            )
            current.lease_owner = None
            current.lease_expires_at = None
            current.last_error_type = type(exc).__name__[:100]
            current.next_attempt_at = datetime.utcnow() + timedelta(
                seconds=_retry_delay_seconds(current.attempt_count)
            )
            _mark_entity_failed(db, current)
            db.commit()
        logger.warning(
            "索引任务失败: job_id=%s entity_type=%s operation=%s error_type=%s",
            job_id,
            job.entity_type,
            job.operation,
            type(exc).__name__,
        )
        return False


def process_pending_index_jobs(limit: int = INDEX_JOB_BATCH_SIZE) -> tuple[int, int]:
    """扫描一批到期任务；每个任务使用独立 Session，避免单项失败污染整批。"""
    from db.database import SessionLocal

    discovery = SessionLocal()
    try:
        now = datetime.utcnow()
        job_ids = list(discovery.scalars(
            select(IndexJob.id)
            .where(
                IndexJob.status.in_(("pending", "failed", "running")),
                IndexJob.next_attempt_at <= now,
                or_(IndexJob.lease_expires_at.is_(None), IndexJob.lease_expires_at <= now),
            )
            .order_by(IndexJob.next_attempt_at, IndexJob.id)
            .limit(limit)
        ))
    finally:
        discovery.close()

    completed = 0
    for job_id in job_ids:
        db = SessionLocal()
        try:
            completed += int(run_index_job(db, job_id))
        finally:
            db.close()
    return completed, len(job_ids)
