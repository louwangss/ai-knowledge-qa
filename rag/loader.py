"""文档加载：根据后缀自动选择 Loader，支持 6 种格式"""
from pathlib import Path

from langchain_core.documents import Document
from langchain_community.document_loaders import (
    PyMuPDFLoader,
    Docx2txtLoader,
    TextLoader,
)

# 格式 -> Loader 映射（.html 和 .htm 同格式）
LOADERS = {
    ".pdf": PyMuPDFLoader,
    ".docx": Docx2txtLoader,
    ".txt": TextLoader,
}


def load_document(file_path: str) -> list[Document]:
    """根据后缀自动选择 Loader，返回 Document 列表"""
    ext = Path(file_path).suffix.lower()

    if ext in (".md",):
        # Markdown 本质是纯文本，用 TextLoader 避免依赖 unstructured
        loader = TextLoader(file_path, encoding="utf-8")
        return loader.load()

    if ext in (".html", ".htm"):
        # 用标准库解析 HTML，避免依赖 lxml
        from html.parser import HTMLParser

        class _HTMLTextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._parts = []
            def handle_data(self, data):
                self._parts.append(data)
            def get_text(self):
                return "".join(self._parts).strip()

        with open(file_path, encoding="utf-8") as f:
            raw = f.read()
        extractor = _HTMLTextExtractor()
        extractor.feed(raw)
        return [Document(page_content=extractor.get_text(), metadata={"source": file_path})]

    loader_class = LOADERS.get(ext)
    if not loader_class:
        raise ValueError(f"不支持的文件格式: {ext}")
    loader = loader_class(file_path)
    return loader.load()
