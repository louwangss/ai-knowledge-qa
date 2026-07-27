"""文档上传资源边界与跨存储删除一致性测试。"""

import hashlib
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from langchain_core.documents import Document as LangChainDocument
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_documents
from db.database import Base
from db.models import Document, User


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    try:
        db.add(User(id="u1", username="owner"))
        db.commit()
        yield db
    finally:
        db.rollback()
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(content))


def _patch_successful_processing(monkeypatch):
    monkeypatch.setattr(
        routes_documents,
        "load_document",
        lambda path: [LangChainDocument(page_content="已解析")],
    )
    monkeypatch.setattr(
        routes_documents,
        "split_text",
        lambda documents: documents,
    )
    monkeypatch.setattr(routes_documents, "add_documents_to_rag", lambda **kwargs: None)


def test_upload_over_limit_returns_413_before_processing(db_session, tmp_path, monkeypatch):
    """超限文件不能进入解析或 embedding，也不能残留文件/记录。"""
    monkeypatch.setattr(routes_documents, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes_documents, "MAX_UPLOAD_BYTES", 8, raising=False)
    load_calls = []

    def fake_load(path):
        load_calls.append(path)
        return [LangChainDocument(page_content="不应处理")]

    monkeypatch.setattr(routes_documents, "load_document", fake_load)
    monkeypatch.setattr(routes_documents, "split_text", lambda documents: documents)
    monkeypatch.setattr(routes_documents, "add_documents_to_rag", lambda **kwargs: None)
    monkeypatch.setattr(routes_documents, "delete_documents_by_mysql_id", lambda doc_id: None)

    with pytest.raises(HTTPException) as exc_info:
        routes_documents.upload_document(
            file=_upload("large.txt", b"123456789"),
            user_id="u1",
            db=db_session,
        )

    assert exc_info.value.status_code == 413
    assert load_calls == []
    assert list(tmp_path.iterdir()) == []
    assert db_session.execute(select(Document)).scalars().all() == []


def test_empty_upload_returns_400_without_residue(db_session, tmp_path, monkeypatch):
    """空文件应在解析前失败，并清理临时文件。"""
    monkeypatch.setattr(routes_documents, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes_documents, "MAX_UPLOAD_BYTES", 1024, raising=False)
    load_calls = []

    monkeypatch.setattr(
        routes_documents,
        "load_document",
        lambda path: load_calls.append(path),
    )

    with pytest.raises(HTTPException) as exc_info:
        routes_documents.upload_document(
            file=_upload("empty.txt", b""),
            user_id="u1",
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert load_calls == []
    assert list(tmp_path.iterdir()) == []
    assert db_session.execute(select(Document)).scalars().all() == []


def test_upload_sanitizes_display_filename(db_session, tmp_path, monkeypatch):
    """客户端路径不能原样进入文档元数据。"""
    monkeypatch.setattr(routes_documents, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes_documents, "MAX_UPLOAD_BYTES", 1024, raising=False)
    _patch_successful_processing(monkeypatch)

    result = routes_documents.upload_document(
        file=_upload("../private/notes.txt", b"hello"),
        user_id="u1",
        db=db_session,
    )

    assert result.filename == "notes.txt"
    assert result.file_size == 5
    assert len(list(tmp_path.iterdir())) == 1


def test_processing_failure_cleans_file_record_and_internal_error(db_session, tmp_path, monkeypatch):
    """解析失败且向量清理成功时，不保留文件/记录，也不回显内部异常。"""
    monkeypatch.setattr(routes_documents, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes_documents, "MAX_UPLOAD_BYTES", 1024, raising=False)
    monkeypatch.setattr(
        routes_documents,
        "load_document",
        lambda path: (_ for _ in ()).throw(RuntimeError("C:/private/path parse failed")),
    )
    monkeypatch.setattr(routes_documents, "delete_documents_by_mysql_id", lambda doc_id: None)

    with pytest.raises(HTTPException) as exc_info:
        routes_documents.upload_document(
            file=_upload("broken.txt", b"not valid"),
            user_id="u1",
            db=db_session,
        )

    assert exc_info.value.status_code == 500
    assert "private" not in str(exc_info.value.detail).lower()
    assert "parse failed" not in str(exc_info.value.detail).lower()
    assert list(tmp_path.iterdir()) == []
    assert db_session.execute(select(Document)).scalars().all() == []


def test_duplicate_upload_leaves_no_temporary_file(db_session, tmp_path, monkeypatch):
    """计算哈希后发现重复时，应删除本次临时文件。"""
    content = b"same content"
    existing_path = tmp_path / "existing.txt"
    existing_path.write_bytes(content)
    db_session.add(Document(
        id="doc-existing",
        user_id="u1",
        filename="existing.txt",
        file_type=".txt",
        file_path=str(existing_path),
        file_size=len(content),
        chunk_size=1000,
        chunk_overlap=200,
        content_hash=hashlib.sha256(content).hexdigest(),
        status="ready",
    ))
    db_session.commit()
    monkeypatch.setattr(routes_documents, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes_documents, "MAX_UPLOAD_BYTES", 1024, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        routes_documents.upload_document(
            file=_upload("duplicate.txt", content),
            user_id="u1",
            db=db_session,
        )

    assert exc_info.value.status_code == 409
    assert [path.name for path in tmp_path.iterdir()] == ["existing.txt"]


def test_partial_vector_cleanup_failure_keeps_recoverable_failed_record(
    db_session,
    tmp_path,
    monkeypatch,
):
    """向量写入和补偿均失败时，保留失败记录及源文件供后续恢复。"""
    monkeypatch.setattr(routes_documents, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes_documents, "MAX_UPLOAD_BYTES", 1024, raising=False)
    monkeypatch.setattr(
        routes_documents,
        "load_document",
        lambda path: [LangChainDocument(page_content="已解析")],
    )
    monkeypatch.setattr(routes_documents, "split_text", lambda documents: documents)
    monkeypatch.setattr(
        routes_documents,
        "add_documents_to_rag",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("partial vector write")),
    )
    monkeypatch.setattr(
        routes_documents,
        "delete_documents_by_mysql_id",
        lambda doc_id: (_ for _ in ()).throw(RuntimeError("vector cleanup failed")),
    )

    with pytest.raises(HTTPException) as exc_info:
        routes_documents.upload_document(
            file=_upload("recoverable.txt", b"source content"),
            user_id="u1",
            db=db_session,
        )

    records = db_session.execute(select(Document)).scalars().all()
    assert exc_info.value.status_code == 500
    assert "partial vector" not in str(exc_info.value.detail).lower()
    assert len(records) == 1
    assert records[0].status == "failed"
    paths = list(tmp_path.iterdir())
    assert len(paths) == 1
    assert paths[0].suffix == ".txt"
    assert not any(path.name.endswith(".part") for path in paths)


def test_chroma_delete_failure_preserves_database_and_source_file(db_session, tmp_path, monkeypatch):
    """派生向量删除失败时，MySQL 权威记录和源文件必须保持不变。"""
    source_path = tmp_path / "source.txt"
    source_path.write_text("source", encoding="utf-8")
    db_session.add(Document(
        id="doc-1",
        user_id="u1",
        filename="source.txt",
        file_type=".txt",
        file_path=str(source_path),
        file_size=6,
        chunk_size=1000,
        chunk_overlap=200,
        content_hash=hashlib.sha256(b"source").hexdigest(),
        status="ready",
    ))
    db_session.commit()
    monkeypatch.setattr(
        routes_documents,
        "delete_documents_by_mysql_id",
        lambda doc_id: (_ for _ in ()).throw(RuntimeError("chroma unavailable")),
    )

    with pytest.raises(HTTPException) as exc_info:
        routes_documents.delete_document("doc-1", user_id="u1", db=db_session)

    assert exc_info.value.status_code == 503
    assert db_session.get(Document, "doc-1") is not None
    assert source_path.exists()


def test_successful_delete_removes_database_and_source_file(db_session, tmp_path, monkeypatch):
    """Chroma 删除成功后，再删除 MySQL 记录和本地源文件。"""
    source_path = tmp_path / "source.txt"
    source_path.write_text("source", encoding="utf-8")
    db_session.add(Document(
        id="doc-1",
        user_id="u1",
        filename="source.txt",
        file_type=".txt",
        file_path=str(source_path),
        file_size=6,
        chunk_size=1000,
        chunk_overlap=200,
        content_hash=hashlib.sha256(b"source").hexdigest(),
        status="ready",
    ))
    db_session.commit()
    monkeypatch.setattr(routes_documents, "delete_documents_by_mysql_id", lambda doc_id: None)

    response = routes_documents.delete_document("doc-1", user_id="u1", db=db_session)

    assert response == {"detail": "删除成功"}
    assert db_session.get(Document, "doc-1") is None
    assert not source_path.exists()
