"""向量化和存储：双 collection 设计，metadata 用户隔离"""
import chromadb
from langchain_core.documents import Document
from langchain_chroma import Chroma

from config import CHROMA_PERSIST_DIR, EMBEDDING_MODEL

# Collection 名称
RAG_COLLECTION = "rag_documents"
SEMANTIC_COLLECTION = "semantic_memory"

_embeddings = None
_rag_vs: Chroma | None = None
_semantic_vs: Chroma | None = None


def get_embeddings():
    """单例嵌入模型"""
    global _embeddings
    if _embeddings is None:
        from langchain_huggingface import HuggingFaceEmbeddings
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            encode_kwargs={"normalize_embeddings": True},
        )
    return _embeddings


def _get_chroma_client():
    """获取持久化 Chroma 客户端"""
    return chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)


def get_rag_vector_store() -> Chroma:
    """获取 RAG 文档向量库（单例）"""
    global _rag_vs
    if _rag_vs is None:
        _rag_vs = Chroma(
            collection_name=RAG_COLLECTION,
            embedding_function=get_embeddings(),
            persist_directory=CHROMA_PERSIST_DIR,
        )
    return _rag_vs


def get_semantic_vector_store() -> Chroma:
    """获取语义记忆向量库（单例）"""
    global _semantic_vs
    if _semantic_vs is None:
        _semantic_vs = Chroma(
            collection_name=SEMANTIC_COLLECTION,
            embedding_function=get_embeddings(),
            persist_directory=CHROMA_PERSIST_DIR,
        )
    return _semantic_vs


def add_documents_to_rag(
    user_id: str,
    mysql_id: str,
    source: str,
    chunks: list[Document],
    created_at: str,
    index_version: int = 1,
):
    """使用稳定 ID 幂等覆盖文档分块，并清理旧版本或多余分块。"""
    vs = get_rag_vector_store()
    existing = vs.get(where={"mysql_id": mysql_id})
    existing_ids = set(existing.get("ids", [])) if existing else set()
    metadatas = []
    stable_ids = [f"document-{mysql_id}-chunk-{index}" for index in range(len(chunks))]
    for index, chunk in enumerate(chunks):
        meta = dict(chunk.metadata) if chunk.metadata else {}
        meta.update({
            "user_id": user_id,
            "mysql_id": mysql_id,
            "type": "document",
            "source": source,
            "created_at": created_at,
            "index_version": index_version,
            "chunk_index": index,
        })
        metadatas.append(meta)
    if chunks:
        vs.add_texts(
            texts=[c.page_content for c in chunks],
            metadatas=metadatas,
            ids=stable_ids,
        )
    stale_ids = sorted(existing_ids - set(stable_ids))
    if stale_ids:
        vs.delete(ids=stale_ids)


def delete_documents_by_mysql_id(mysql_id: str):
    """通过 mysql_id 删除 RAG 文档库中的向量"""
    vs = get_rag_vector_store()
    vs.delete(where={"mysql_id": mysql_id})


def delete_semantic_vectors_by_mysql_id(mysql_id: str) -> None:
    """直接删除笔记向量；删除操作不初始化 Embedding 模型。"""
    collection = _get_chroma_client().get_or_create_collection(
        name=SEMANTIC_COLLECTION,
        embedding_function=None,
    )
    collection.delete(where={"mysql_id": mysql_id})
