"""LangGraph 共享状态定义"""
from typing import TypedDict


class RetrievedDoc(TypedDict):
    content: str
    source: str
    sub_question: str
    score: float


class RetrievedNote(TypedDict):
    content: str
    concept: str


class ResearchState(TypedDict):
    original_question: str
    sub_questions: list[str]
    retrieved_docs: list[RetrievedDoc]
    notes: list[RetrievedNote]
    episodic_memory: list[str]
    short_term_memory: str
    final_answer: str
    user_id: str
