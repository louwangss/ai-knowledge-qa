"""持久化索引任务的状态、租约与失败恢复测试。"""

from datetime import datetime

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import Document, IndexJob, User
from indexing.jobs import enqueue_index_job, run_index_job


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_sqlite_integer(type_, compiler, **kwargs):
    return "INTEGER"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(User(id="u1", username="owner"))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _document() -> Document:
    return Document(
        id="doc-1",
        user_id="u1",
        filename="source.txt",
        file_type=".txt",
        file_path="source.txt",
        file_size=6,
        chunk_size=1000,
        chunk_overlap=200,
        content_hash="a" * 64,
        status="indexing",
        index_version=1,
        indexed_version=0,
    )


def test_enqueue_job_is_durable_and_idempotent(db_session):
    db_session.add(_document())
    first = enqueue_index_job(db_session, "document", "doc-1", "upsert", 1)
    db_session.commit()

    second = enqueue_index_job(db_session, "document", "doc-1", "upsert", 1)
    db_session.commit()

    jobs = db_session.scalars(select(IndexJob)).all()
    assert first.id == second.id
    assert len(jobs) == 1
    assert jobs[0].status == "pending"


def test_failed_job_keeps_source_and_schedules_retry(db_session, monkeypatch):
    document = _document()
    db_session.add(document)
    job = enqueue_index_job(db_session, "document", document.id, "upsert", 1)
    db_session.commit()

    monkeypatch.setattr(
        "app.api.routes_documents.process_document_index_job",
        lambda db, claimed_job: (_ for _ in ()).throw(RuntimeError("chroma down")),
    )

    assert run_index_job(db_session, job.id) is False

    db_session.expire_all()
    persisted_job = db_session.get(IndexJob, job.id)
    assert db_session.get(Document, document.id) is not None
    assert persisted_job.status == "failed"
    assert persisted_job.attempt_count == 1
    assert persisted_job.next_attempt_at > datetime.utcnow()
    assert persisted_job.last_error_type == "RuntimeError"


def test_job_stops_after_configured_attempt_limit(db_session, monkeypatch):
    document = _document()
    db_session.add(document)
    job = enqueue_index_job(db_session, "document", document.id, "upsert", 1)
    db_session.commit()
    monkeypatch.setattr("indexing.jobs.INDEX_JOB_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(
        "app.api.routes_documents.process_document_index_job",
        lambda db, claimed_job: (_ for _ in ()).throw(RuntimeError("permanent")),
    )

    assert run_index_job(db_session, job.id) is False

    db_session.expire_all()
    assert db_session.get(IndexJob, job.id).status == "exhausted"
