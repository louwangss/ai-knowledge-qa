"""normal/deep 共用的检索结果可信度策略。"""

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import Document, SemanticMemory, User
from rag.retrieval_policy import filter_authoritative_results


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_sqlite_integer(type_, compiler, **kwargs):
    return "INTEGER"


def test_policy_rejects_stale_failed_and_unknown_vectors():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id="u1", username="owner"))
        db.add_all([
            Document(
                id="ready",
                user_id="u1",
                filename="ready.txt",
                file_type=".txt",
                file_path="ready.txt",
                chunk_size=1000,
                chunk_overlap=200,
                content_hash="a" * 64,
                status="ready",
                index_version=2,
                indexed_version=2,
            ),
            Document(
                id="failed",
                user_id="u1",
                filename="failed.txt",
                file_type=".txt",
                file_path="failed.txt",
                chunk_size=1000,
                chunk_overlap=200,
                content_hash="b" * 64,
                status="failed",
                index_version=1,
                indexed_version=0,
            ),
        ])
        db.commit()

    candidates = [
        {"content": "有效", "metadata": {"mysql_id": "ready", "index_version": 2}, "score": 0.9},
        {"content": "旧版本", "metadata": {"mysql_id": "ready", "index_version": 1}, "score": 0.99},
        {"content": "失败记录", "metadata": {"mysql_id": "failed", "index_version": 1}, "score": 0.99},
        {"content": "孤儿", "metadata": {"mysql_id": "missing", "index_version": 1}, "score": 0.99},
    ]
    try:
        assert filter_authoritative_results(
            candidates,
            user_id="u1",
            doc_type="document",
            session_factory=factory,
            threshold=0.5,
        ) == [candidates[0]]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_policy_deduplicates_by_stable_chunk_identity():
    class Entity:
        id = "ready"
        status = "ready"
        index_version = 2
        indexed_version = 2

    class Scalars:
        def all(self):
            return [Entity()]

    class Session:
        def scalars(self, statement):
            return Scalars()

        def close(self):
            pass

    candidates = [
        {"content": "同一块", "metadata": {"mysql_id": "ready", "index_version": 2, "chunk_index": 0}, "score": 0.9},
        {"content": "重复结果", "metadata": {"mysql_id": "ready", "index_version": 2, "chunk_index": 0}, "score": 0.8},
    ]

    result = filter_authoritative_results(
        candidates,
        user_id="u1",
        doc_type="document",
        session_factory=Session,
        threshold=0.5,
    )

    assert result == [candidates[0]]
