"""文档路由：POST 上传、GET 列表、DELETE 删除"""
import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_db, require_app_user
from app.models.schemas import DocumentResponse
from config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from db.models import Document, User
from rag.loader import load_document
from rag.splitter import split_text
from rag.vector_store import add_documents_to_rag, delete_documents_by_mysql_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
ALLOWED_EXTS = {".pdf", ".docx", ".md", ".txt", ".html", ".htm"}


def _normalize_filename(filename: str | None) -> tuple[str, str]:
    """只保留客户端文件名的 basename，并验证长度和扩展名。"""
    display_name = Path((filename or "").replace("\\", "/")).name
    if not display_name or len(display_name) > 255:
        raise HTTPException(status_code=400, detail="文件名无效")

    ext = Path(display_name).suffix.lower()
    if ext not in ALLOWED_EXTS:
        supported = ", ".join(sorted(ALLOWED_EXTS))
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {ext}，支持: {supported}")
    return display_name, ext


def _write_upload_to_temp(file: UploadFile, temp_path: Path) -> tuple[int, str]:
    """分块写入临时文件并计算哈希，超限或失败时自动清理。"""
    total_bytes = 0
    content_hasher = hashlib.sha256()

    try:
        with temp_path.open("xb") as output:
            while True:
                chunk = file.file.read(UPLOAD_READ_CHUNK_BYTES)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="文件超过允许大小")
                content_hasher.update(chunk)
                output.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="文件内容为空")
        return total_bytes, content_hasher.hexdigest()
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@router.post("", response_model=DocumentResponse)
def upload_document(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    db: Session = Depends(get_db),
):
    require_app_user(user_id)
    # 验证用户
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    display_filename, ext = _normalize_filename(file.filename)

    # 同目录临时落盘，确保最终改名是原子操作
    upload_dir = Path(UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4()}{ext}"
    file_path = upload_dir / stored_filename
    temp_path = upload_dir / f".{stored_filename}.part"
    file_size, content_hash = _write_upload_to_temp(file, temp_path)

    # 检查重复（UNIQUE INDEX idx_doc_hash）
    try:
        existing = db.query(Document).filter(
            Document.user_id == user_id,
            Document.content_hash == content_hash,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"文档已存在: {existing.filename}")
        temp_path.replace(file_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    # 创建文档记录（status=processing）
    doc = Document(
        id=str(uuid.uuid4()),
        user_id=user_id,
        filename=display_filename,
        file_type=ext,
        file_path=str(file_path),
        file_size=file_size,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        content_hash=content_hash,
        status="processing",
    )
    db.add(doc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="文档已存在（内容哈希重复）")
    except Exception:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise

    # 处理文档：加载 → 切片 → 向量化
    vector_write_attempted = False
    try:
        documents = load_document(str(file_path))
        chunks = split_text(documents)
        vector_write_attempted = True
        add_documents_to_rag(
            user_id=user_id,
            mysql_id=doc.id,
            source=display_filename,
            chunks=chunks,
            created_at=datetime.utcnow().isoformat(),
        )
        doc.chunk_count = len(chunks)
        doc.status = "ready"
        db.commit()
    except Exception as exc:
        logger.error(
            "文档处理失败: document_id=%s, error_type=%s",
            doc.id,
            type(exc).__name__,
        )
        vectors_cleaned = not vector_write_attempted
        if vector_write_attempted:
            try:
                delete_documents_by_mysql_id(doc.id)
                vectors_cleaned = True
            except Exception as cleanup_exc:
                logger.warning(
                    "失败文档的部分向量清理失败: document_id=%s, error_type=%s",
                    doc.id,
                    type(cleanup_exc).__name__,
                )

        db.rollback()
        persisted_doc = db.get(Document, doc.id)
        if vectors_cleaned:
            try:
                if persisted_doc is not None:
                    db.delete(persisted_doc)
                    db.commit()
                file_path.unlink(missing_ok=True)
            except Exception as cleanup_exc:
                db.rollback()
                logger.warning(
                    "失败文档的源记录清理失败: document_id=%s, error_type=%s",
                    doc.id,
                    type(cleanup_exc).__name__,
                )
        elif persisted_doc is not None:
            try:
                persisted_doc.status = "failed"
                db.commit()
            except Exception as cleanup_exc:
                db.rollback()
                logger.warning(
                    "失败文档状态更新失败: document_id=%s, error_type=%s",
                    doc.id,
                    type(cleanup_exc).__name__,
                )

        raise HTTPException(status_code=500, detail="文档处理失败，请检查文件内容或稍后重试")

    return doc


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    require_app_user(user_id)
    return db.query(Document).filter(
        Document.user_id == user_id,
        Document.status == "ready",
    ).order_by(Document.created_at.desc()).all()


@router.delete("/{document_id}")
def delete_document(document_id: str, user_id: str = Query(...), db: Session = Depends(get_db)):
    require_app_user(user_id)
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == user_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    source_path = Path(doc.file_path)

    # 先删除派生向量；失败时保留 MySQL 权威记录和源文件，允许安全重试。
    try:
        delete_documents_by_mysql_id(doc.id)
    except Exception as exc:
        logger.warning(
            "删除文档向量失败: document_id=%s, error_type=%s",
            doc.id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="文档删除暂时失败，请稍后重试")

    try:
        db.delete(doc)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "文档数据库记录删除失败: document_id=%s, error_type=%s",
            document_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="文档删除暂时失败，请稍后重试")

    # 数据库与向量状态已一致；本地文件失败只会形成可人工清理的磁盘孤儿。
    try:
        source_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "文档记录已删除，但本地文件清理失败: document_id=%s, error_type=%s",
            document_id,
            type(exc).__name__,
        )

    return {"detail": "删除成功"}
