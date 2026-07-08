"""文档路由：POST 上传、GET 列表、DELETE 删除"""
import hashlib
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.schemas import DocumentResponse
from config import UPLOAD_DIR
from db.models import Document, User
from rag.loader import load_document
from rag.splitter import split_text
from rag.vector_store import add_documents_to_rag, delete_documents_by_mysql_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


@router.post("", response_model=DocumentResponse)
def upload_document(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    db: Session = Depends(get_db),
):
    # 验证用户
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 校验文件类型
    ext = Path(file.filename).suffix.lower()
    allowed_exts = {".pdf", ".docx", ".md", ".txt", ".html", ".htm"}
    if ext not in allowed_exts:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {ext}，支持: {', '.join(sorted(allowed_exts))}")

    # 读取文件内容
    content_bytes = file.file.read()
    if not content_bytes:
        raise HTTPException(status_code=400, detail="文件内容为空")

    content_hash = hashlib.sha256(content_bytes).hexdigest()

    # 检查重复（UNIQUE INDEX idx_doc_hash）
    existing = db.query(Document).filter(
        Document.user_id == user_id,
        Document.content_hash == content_hash,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"文档已存在: {existing.filename}")

    # UUID 重命名保存
    stored_filename = f"{uuid.uuid4()}{ext}"
    upload_dir = Path(UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / stored_filename
    file_path.write_bytes(content_bytes)

    # 创建文档记录（status=processing）
    doc = Document(
        id=str(uuid.uuid4()),
        user_id=user_id,
        filename=file.filename,
        file_type=ext,
        file_path=str(file_path),
        file_size=len(content_bytes),
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
        raise HTTPException(status_code=409, detail="文档已存在（内容哈希重复）")
    db.refresh(doc)

    # 处理文档：加载 → 切片 → 向量化
    try:
        documents = load_document(str(file_path))
        chunks = split_text(documents)
        add_documents_to_rag(
            user_id=user_id,
            mysql_id=doc.id,
            source=file.filename,
            chunks=chunks,
            created_at=datetime.utcnow().isoformat(),
        )
        doc.chunk_count = len(chunks)
        doc.status = "ready"
        db.commit()
        db.refresh(doc)
    except Exception as e:
        logger.error(f"文档处理失败: {e}", exc_info=True)
        doc.status = "failed"
        db.commit()
        db.refresh(doc)
        # 清理已写入的部分向量
        try:
            delete_documents_by_mysql_id(doc.id)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"文档处理失败: {e}")

    return doc


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    return db.query(Document).filter(
        Document.user_id == user_id,
        Document.status == "ready",
    ).order_by(Document.created_at.desc()).all()


@router.delete("/{document_id}")
def delete_document(document_id: str, user_id: str = Query(...), db: Session = Depends(get_db)):
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == user_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 删除 Chroma 向量
    try:
        delete_documents_by_mysql_id(doc.id)
    except Exception as e:
        logger.warning(f"删除向量失败: {e}")

    # 删除本地文件
    try:
        os.remove(doc.file_path)
    except Exception:
        pass

    # 删除 MySQL 记录
    db.delete(doc)
    db.commit()
    return {"detail": "删除成功"}
