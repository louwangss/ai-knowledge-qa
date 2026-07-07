"""文档加载：根据后缀自动选择 Loader，支持 6 种格式"""
from pathlib import Path

from langchain_core.documents import Document
from langchain_community.document_loaders import (
    PyMuPDFLoader,
    Docx2txtLoader,
    UnstructuredMarkdownLoader,
    BSHTMLLoader,
    TextLoader,
)

# 格式 -> Loader 映射（.html 和 .htm 同格式）
LOADERS = {
    ".pdf": PyMuPDFLoader,
    ".docx": Docx2txtLoader,
    ".md": UnstructuredMarkdownLoader,
    ".html": BSHTMLLoader,
    ".htm": BSHTMLLoader,
    ".txt": TextLoader,
}


def load_document(file_path: str) -> list[Document]:
    """根据后缀自动选择 Loader，返回 Document 列表"""
    ext = Path(file_path).suffix.lower()
    loader_class = LOADERS.get(ext)
    if not loader_class:
        raise ValueError(f"不支持的文件格式: {ext}")
    loader = loader_class(file_path)
    return loader.load()
