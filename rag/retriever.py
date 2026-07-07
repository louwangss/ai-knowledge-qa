"""检索组件：基础检索器 + 高级检索器（可选）"""
from langchain_chroma import Chroma
from langchain_core.retrievers import BaseRetriever
from langchain_openai import ChatOpenAI
from langchain_classic.retrievers import MultiQueryRetriever


def get_basic_retriever(
    vector_store: Chroma,
    user_id: str,
    top_k: int = 3,
    doc_type: str = "document",
) -> BaseRetriever:
    """返回基础向量检索器

    Args:
        doc_type: "document" 只检索文档，"note" 只检索笔记
    """
    return vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={
            "k": top_k,
            "filter": {"$and": [{"user_id": user_id}, {"type": doc_type}]},
        },
    )


def get_advanced_retriever(
    vector_store: Chroma,
    llm: ChatOpenAI,
    user_id: str,
    top_k: int = 3,
) -> BaseRetriever:
    """返回带 MQE 的高级检索器（可选，默认不启用）"""
    mq_retriever = MultiQueryRetriever.from_llm(
        llm=llm,
        retriever=get_basic_retriever(vector_store, user_id, top_k),
    )
    return mq_retriever
