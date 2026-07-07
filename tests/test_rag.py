"""RAG 模块测试：splitter 分块行为 + loader 格式路由"""
from langchain_core.documents import Document

from rag.splitter import split_text
from rag.loader import LOADERS


# ---- splitter ----

def test_split_text_short_no_split():
    """短文本不拆分"""
    docs = [Document(page_content="这是一段简短的文本。")]
    chunks = split_text(docs)
    assert len(chunks) == 1


def test_split_text_long_splits():
    """长文本（超过 chunk_size=1000）应拆分为多块"""
    long_text = "A" * 2500
    docs = [Document(page_content=long_text)]
    chunks = split_text(docs)
    assert len(chunks) >= 2


def test_split_text_preserves_content():
    """分块后内容总长度应覆盖原文（考虑 overlap）"""
    text = "B" * 1800
    docs = [Document(page_content=text)]
    chunks = split_text(docs)
    total = sum(len(c.page_content) for c in chunks)
    # 有 overlap，总长度 >= 原文长度
    assert total >= 1800


def test_split_text_multiple_docs():
    """多文档分块各自独立"""
    docs = [
        Document(page_content="文档1 " * 200),
        Document(page_content="文档2 " * 200),
    ]
    chunks = split_text(docs)
    assert len(chunks) >= 2


# ---- loader 格式路由 ----

def test_loader_has_all_formats():
    """6 个后缀全部支持"""
    expected = {".pdf", ".docx", ".md", ".html", ".htm", ".txt"}
    assert expected.issubset(set(LOADERS.keys()))


def test_loader_html_htm_same_loader():
    """html 和 htm 使用同一个 Loader"""
    assert LOADERS[".html"] == LOADERS[".htm"]


def test_loader_pdf_uses_pymupdf():
    """pdf 使用 PyMuPDFLoader"""
    from langchain_community.document_loaders import PyMuPDFLoader
    assert LOADERS[".pdf"] == PyMuPDFLoader


# ---- 相关度阈值过滤 ----

from memory.retrieve import _filter_by_relevance


def test_filter_by_relevance_keeps_above_threshold():
    """相关度 >= 阈值的文档保留"""
    docs = [
        {"content": "相关文档A", "metadata": {}, "score": 0.8},
        {"content": "相关文档B", "metadata": {}, "score": 0.5},
        {"content": "不相关文档", "metadata": {}, "score": 0.3},
    ]
    result = _filter_by_relevance(docs, threshold=0.5)
    assert len(result) == 2
    assert result[0]["content"] == "相关文档A"
    assert result[1]["content"] == "相关文档B"


def test_filter_by_relevance_empty_input():
    """空列表返回空列表"""
    result = _filter_by_relevance([], threshold=0.5)
    assert result == []


def test_filter_by_relevance_all_filtered():
    """全部低于阈值时返回空列表"""
    docs = [
        {"content": "文档1", "metadata": {}, "score": 0.1},
        {"content": "文档2", "metadata": {}, "score": 0.2},
    ]
    result = _filter_by_relevance(docs, threshold=0.5)
    assert result == []
