"""笔记保存与向量索引一致性测试。"""

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.schemas import NoteCreate, NoteUpdate
from db.database import Base
from db.models import SemanticMemory, User
from memory import semantic


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_sqlite_integer(type_, compiler, **kwargs):
    """让 MySQL BIGINT 主键在 SQLite 测试中保留自增语义。"""
    return "INTEGER"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id="u1", username="user"))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_note_schema_accepts_empty_draft_and_rejects_oversized_fields():
    draft = NoteCreate(user_id="u1", concept="", content="")
    assert draft.content == ""

    with pytest.raises(ValidationError):
        NoteCreate(user_id="u1", concept="题" * 101, content="")

    with pytest.raises(ValidationError):
        NoteUpdate(content="中" * 21846)


def test_create_note_only_writes_mysql(db_session):
    with patch.object(
        semantic,
        "get_semantic_vector_store",
        side_effect=AssertionError("创建草稿不应同步访问向量库"),
    ):
        note = semantic.create_note(db_session, "u1", "", "")

    assert note.id is not None
    assert note.content == ""
    assert note.chroma_id == ""


def test_update_note_marks_index_dirty_without_syncing_chroma(db_session):
    note = SemanticMemory(
        user_id="u1",
        concept="旧标题",
        content="旧内容",
        chroma_id="legacy-vector-id",
    )
    db_session.add(note)
    db_session.commit()

    with patch.object(
        semantic,
        "get_semantic_vector_store",
        side_effect=AssertionError("保存请求不应同步访问向量库"),
    ):
        updated = semantic.update_note(
            db_session,
            note.id,
            "u1",
            "新标题",
            "新内容",
        )

    assert updated.concept == "新标题"
    assert updated.content == "新内容"
    assert updated.chroma_id is None


def test_sync_to_chroma_uses_stable_id_and_removes_legacy_vectors(db_session):
    note = SemanticMemory(
        user_id="u1",
        concept="标题",
        content="正文",
        chroma_id=None,
    )
    db_session.add(note)
    db_session.commit()

    class FakeVectorStore:
        def __init__(self):
            self.added = None
            self.deleted = []

        def get(self, **kwargs):
            return {"ids": ["legacy-vector-id"]}

        def add_texts(self, **kwargs):
            self.added = kwargs
            return kwargs["ids"]

        def delete(self, ids):
            self.deleted.extend(ids)

    vector_store = FakeVectorStore()
    with patch.object(semantic, "get_semantic_vector_store", return_value=vector_store):
        semantic._sync_to_chroma(db_session, note)

    stable_id = f"semantic-note-{note.id}"
    assert vector_store.added["ids"] == [stable_id]
    assert vector_store.deleted == ["legacy-vector-id"]
    assert note.chroma_id == stable_id


def test_delete_semantic_vectors_does_not_load_embeddings(monkeypatch):
    from rag import vector_store

    deleted = {}

    class FakeCollection:
        def delete(self, **kwargs):
            deleted.update(kwargs)

    class FakeClient:
        def get_or_create_collection(self, name, embedding_function):
            assert name == vector_store.SEMANTIC_COLLECTION
            assert embedding_function is None
            return FakeCollection()

    monkeypatch.setattr(vector_store, "_get_chroma_client", lambda: FakeClient())
    monkeypatch.setattr(
        vector_store,
        "get_embeddings",
        lambda: (_ for _ in ()).throw(AssertionError("删除向量不应加载 Embedding")),
    )

    vector_store.delete_semantic_vectors_by_mysql_id("42")

    assert deleted == {"where": {"mysql_id": "42"}}


def test_delete_note_keeps_mysql_record_when_vector_cleanup_fails(db_session):
    note = semantic.create_note(db_session, "u1", "标题", "正文")

    with patch.object(
        semantic,
        "_delete_note_vectors",
        side_effect=RuntimeError("Chroma unavailable"),
    ):
        with pytest.raises(RuntimeError, match="Chroma unavailable"):
            semantic.delete_note(db_session, note.id, "u1")

    assert db_session.get(SemanticMemory, note.id) is not None
