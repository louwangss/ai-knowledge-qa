"""文档路由：POST 上传、GET 列表、DELETE 删除"""
import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_db, require_app_user
from app.models.schemas import DocumentResponse
from config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from db.models import Document, IndexJob, User
from indexing.jobs import enqueue_index_job, run_index_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
ALLOWED_EXTS = {".pdf", ".docx", ".md", ".txt", ".html", ".htm"}


def load_document(file_path: str):
    """按需加载文档解析依赖，并保留稳定的测试替换点。"""
    from rag.loader import load_document as load

    return load(file_path)


def split_text(documents):
    """按需加载文本分块依赖。"""
    from rag.splitter import split_text as split

    return split(documents)


def add_documents_to_rag(**kwargs):
    """按需加载向量写入依赖。"""
    from rag.vector_store import add_documents_to_rag as add

    return add(**kwargs)


def delete_documents_by_mysql_id(mysql_id: str):
    """按需加载向量删除依赖。"""
    from rag.vector_store import delete_documents_by_mysql_id as delete

    return delete(mysql_id)


def process_document_index_job(db: Session, job: IndexJob) -> None:
    """执行已持久化的文档索引任务；任务版本过期时安全跳过。"""
    doc = db.get(Document, job.entity_id)
    if doc is None or doc.index_version != job.desired_version:
        return

    if job.operation == "delete":
        delete_documents_by_mysql_id(doc.id)
        Path(doc.file_path).unlink(missing_ok=True)
        db.delete(doc)
        return

    if job.operation != "upsert":
        raise ValueError(f"不支持的文档索引操作: {job.operation}")

    doc.status = "indexing"
    db.commit()
    documents = load_document(doc.file_path)
    chunks = split_text(documents)
    add_documents_to_rag(
        user_id=doc.user_id,
        mysql_id=doc.id,
        source=doc.filename,
        chunks=chunks,
        created_at=doc.created_at.isoformat() if doc.created_at else "",
        index_version=job.desired_version,
    )
    doc = db.get(Document, job.entity_id)
    if doc is not None and doc.index_version == job.desired_version:
        doc.chunk_count = len(chunks)
        doc.indexed_version = job.desired_version
        doc.status = "ready"


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

    # 权威记录与索引任务在同一事务提交，进程崩溃后仍可继续处理。
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
        status="indexing",
        index_version=1,
        indexed_version=0,
    )
    db.add(doc)
    try:
        job = enqueue_index_job(db, "document", doc.id, "upsert", doc.index_version)
        db.commit()
    except IntegrityError:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="文档已存在（内容哈希重复）")
    except Exception:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise

    if not run_index_job(db, job.id):
        raise HTTPException(
            status_code=503,
            detail="文档已保存，索引暂时失败并已进入后台重试队列",
        )
    return db.get(Document, doc.id)


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    require_app_user(user_id)
    return db.query(Document).filter(
        Document.user_id == user_id,
        Document.status != "deleting",
    ).order_by(Document.created_at.desc()).all()


@router.delete("/{document_id}", status_code=202)
def delete_document(document_id: str, user_id: str = Query(...), db: Session = Depends(get_db)):
    require_app_user(user_id)
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == user_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    if doc.status == "deleting":
        return {"detail": "删除任务已在队列中"}
    doc.index_version += 1
    doc.status = "deleting"
    job = enqueue_index_job(db, "document", doc.id, "delete", doc.index_version)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "文档数据库记录删除失败: document_id=%s, error_type=%s",
            document_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="文档删除暂时失败，请稍后重试")

    if run_index_job(db, job.id):
        return {"detail": "删除成功"}
    return {"detail": "删除任务已入队，将在后台重试"}
