"""并行上下文检索的数据库 Session 隔离测试。"""

import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from memory import retrieve


class FakeSession:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


class FakeSessionFactory:
    def __init__(self):
        self.created = []

    def __call__(self):
        session = FakeSession(f"db-{len(self.created) + 1}")
        self.created.append(session)
        return session


class FakeVectorStore:
    def __init__(self, content, doc_type):
        self.content = content
        self.doc_type = doc_type
        self.calls = 0

    def similarity_search_with_relevance_scores(self, question, k, filter):
        self.calls += 1
        document = SimpleNamespace(
            page_content=self.content,
            metadata={"source": f"{self.doc_type}.md", "type": self.doc_type},
        )
        return [(document, 0.9)]


def test_retrieve_context_uses_distinct_worker_sessions_and_closes_them():
    """episodic 与短期记忆线程各自创建并关闭数据库 Session。"""
    session_factory = FakeSessionFactory()
    sessions_seen = []

    def fake_recent_events(db, user_id, limit):
        sessions_seen.append(db)
        return [SimpleNamespace(content="学习事件", event_type="qa_completed")]

    def fake_conversation(db_session, user_id, session_id):
        sessions_seen.append(db_session)
        return {"summary": None, "messages": []}

    with ExitStack() as stack:
        stack.enter_context(patch.object(retrieve, "SessionLocal", session_factory))
        stack.enter_context(
            patch.object(retrieve, "get_rag_vector_store", return_value=FakeVectorStore("文档", "document"))
        )
        stack.enter_context(
            patch.object(retrieve, "get_semantic_vector_store", return_value=FakeVectorStore("笔记", "note"))
        )
        stack.enter_context(patch.object(retrieve, "get_recent_events", side_effect=fake_recent_events))
        stack.enter_context(patch.object(retrieve, "load_conversation_memory", side_effect=fake_conversation))

        context = asyncio.run(
            retrieve.retrieve_context(
                user_id="u1",
                session_id="s1",
                question="问题",
                mode="normal",
            )
        )

    assert len(session_factory.created) == 2
    assert sessions_seen[0] is not sessions_seen[1]
    assert all(session.closed for session in session_factory.created)
    assert context == {
        "documents": [
            {
                "content": "文档",
                "metadata": {"source": "document.md", "type": "document"},
                "score": 0.9,
            }
        ],
        "notes": [
            {
                "content": "笔记",
                "metadata": {"source": "note.md", "type": "note"},
                "score": 0.9,
            }
        ],
        "episodic_memory": [{"content": "学习事件", "event_type": "qa_completed"}],
        "short_term_memory": {"summary": None, "messages": []},
    }


def test_database_worker_closes_session_when_query_fails():
    """工作线程中的查询异常也必须关闭它创建的 Session。"""
    session_factory = FakeSessionFactory()

    with patch.object(retrieve, "SessionLocal", session_factory), patch.object(
        retrieve,
        "get_recent_events",
        side_effect=RuntimeError("query failed"),
    ):
        try:
            retrieve._load_recent_events("u1", limit=5)
            assert False, "查询失败应向调用方抛出"
        except RuntimeError as exc:
            assert str(exc) == "query failed"

    assert len(session_factory.created) == 1
    assert session_factory.created[0].closed is True


def test_deep_mode_keeps_contract_and_skips_document_search():
    """deep 模式仍返回统一结构，并把文档检索留给 Agent B。"""
    session_factory = FakeSessionFactory()
    document_store = FakeVectorStore("文档", "document")
    note_store = FakeVectorStore("笔记", "note")

    with ExitStack() as stack:
        stack.enter_context(patch.object(retrieve, "SessionLocal", session_factory))
        stack.enter_context(patch.object(retrieve, "get_rag_vector_store", return_value=document_store))
        stack.enter_context(patch.object(retrieve, "get_semantic_vector_store", return_value=note_store))
        stack.enter_context(patch.object(retrieve, "get_recent_events", return_value=[]))
        stack.enter_context(
            patch.object(
                retrieve,
                "load_conversation_memory",
                return_value={"summary": "早期摘要", "messages": []},
            )
        )

        context = asyncio.run(
            retrieve.retrieve_context(
                user_id="u1",
                session_id="s1",
                question="问题",
                mode="deep",
            )
        )

    assert context["documents"] == []
    assert context["notes"][0]["content"] == "笔记"
    assert context["episodic_memory"] == []
    assert context["short_term_memory"] == {"summary": "早期摘要", "messages": []}
    assert document_store.calls == 0
    assert note_store.calls == 1
    assert len(session_factory.created) == 2
    assert all(session.closed for session in session_factory.created)
