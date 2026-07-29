"""Chat 流式生成失败与超时边界的回归测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from app.api import routes_chat
from app.models.schemas import ChatRequest


EMPTY_CONTEXT = {
    "documents": [],
    "notes": [],
    "episodic_memory": [],
    "short_term_memory": {},
}


def _normal_payload() -> ChatRequest:
    return ChatRequest(
        user_id="u1",
        session_id="s1",
        message="请回答这个问题",
        mode="normal",
    )


def test_normal_web_search_is_separate_from_final_answer_model(monkeypatch):
    """联网结果先由服务端取得，再作为只读上下文交给无工具回答模型。"""
    import config

    prompts = []

    class FinalLlm:
        async def astream(self, prompt):
            prompts.append(prompt)
            yield SimpleNamespace(content="最终答案")

    async def fake_search(query):
        return [{
            "url": "https://example.com",
            "title": "公开资料",
            "content": "公开搜索正文",
            "source": "公开资料 - https://example.com",
            "score": 0.8,
        }]

    monkeypatch.setattr(config, "TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(routes_chat, "get_llm", lambda **kwargs: FinalLlm())
    monkeypatch.setattr(
        routes_chat,
        "plan_web_search",
        lambda question, current_date: SimpleNamespace(needs_web=True, query="公开查询"),
    )
    monkeypatch.setattr(routes_chat, "search_web", fake_search)

    async def run():
        events = []
        async for event in routes_chat._stream_normal(_normal_payload(), EMPTY_CONTEXT):
            events.append(event)
        return events

    events = asyncio.run(run())

    assert [event["content"] for event in events if event["type"] == "token"] == ["最终答案"]
    assert [event for event in events if event["type"] == "sources"]
    assert "公开搜索正文" in prompts[0]


def test_web_search_failure_still_uses_private_context_answer_path(monkeypatch):
    """Tavily 短暂失败不影响无工具模型基于本地上下文回答。"""
    import config

    class FinalLlm:
        async def astream(self, prompt):
            yield SimpleNamespace(content="本地上下文答案")

    async def broken_search(query):
        raise RuntimeError("tavily unavailable")

    monkeypatch.setattr(config, "TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(routes_chat, "get_llm", lambda **kwargs: FinalLlm())
    monkeypatch.setattr(
        routes_chat,
        "plan_web_search",
        lambda question, current_date: SimpleNamespace(needs_web=True, query="公开查询"),
    )
    monkeypatch.setattr(routes_chat, "search_web", broken_search)

    async def run():
        return [
            event
            async for event in routes_chat._stream_normal(_normal_payload(), EMPTY_CONTEXT)
        ]

    events = asyncio.run(run())

    assert [event["content"] for event in events if event["type"] == "token"] == [
        "本地上下文答案"
    ]


@pytest.mark.parametrize(
    "partial_tokens",
    [[], ["Agent C 的半截答案"]],
    ids=["before-first-token", "after-partial-answer"],
)
def test_agent_c_llm_failure_propagates_instead_of_completing_partial_answer(
    monkeypatch,
    partial_tokens,
):
    """Agent C 失败必须向图传播，避免空答案或半截答案被标为 completed。"""
    import langgraph.config
    from agents import summarizer

    written_events = []

    class BrokenLlm:
        def stream(self, prompt):
            for token in partial_tokens:
                yield SimpleNamespace(content=token)
            raise RuntimeError("deep llm failed")

    monkeypatch.setattr(summarizer, "get_llm", lambda **kwargs: BrokenLlm())
    monkeypatch.setattr(
        langgraph.config,
        "get_stream_writer",
        lambda: written_events.append,
    )

    with pytest.raises(RuntimeError, match="Agent C LLM 调用失败") as exc_info:
        summarizer.agent_c_summarize({
            "original_question": "深度研究问题",
            "retrieved_docs": [],
            "notes": [],
            "episodic_memory": [],
            "short_term_memory": "",
        })

    assert "deep llm failed" not in str(exc_info.value)
    assert [event["content"] for event in written_events] == partial_tokens


def test_normal_llm_output_uses_idle_timeout(monkeypatch):
    """最终回答模型长时间没有对外进度时必须抛出项目级 idle timeout。"""
    import config

    class HangingLlm:
        async def astream(self, prompt):
            yield SimpleNamespace(content="模型已开始回答")
            await asyncio.Event().wait()

    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(routes_chat, "CHAT_STAGE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(routes_chat, "get_llm", lambda **kwargs: HangingLlm())

    async def consume():
        async for _ in routes_chat._stream_normal(_normal_payload(), EMPTY_CONTEXT):
            pass

    with pytest.raises(TimeoutError, match="0.01"):
        asyncio.run(asyncio.wait_for(consume(), timeout=0.2))


def test_deep_graph_stream_uses_idle_timeout(monkeypatch):
    """Deep 图流长时间无更新时必须抛出项目级 idle timeout。"""
    from agents import graph as graph_module

    class HangingGraph:
        async def astream(self, initial_state, stream_mode):
            await asyncio.Event().wait()
            if False:  # pragma: no cover - 仅把函数声明为异步生成器
                yield None

    monkeypatch.setattr(graph_module, "get_graph", lambda: HangingGraph())
    monkeypatch.setattr(routes_chat, "CHAT_STAGE_TIMEOUT_SECONDS", 0.01)

    payload = ChatRequest(
        user_id="u1",
        session_id="s1",
        message="请做深度研究",
        mode="deep",
    )

    async def consume():
        async for _ in routes_chat._stream_deep(payload, EMPTY_CONTEXT):
            pass

    with pytest.raises(TimeoutError, match="0.01"):
        asyncio.run(asyncio.wait_for(consume(), timeout=0.2))


def test_timeout_error_maps_to_retryable_user_message():
    assert routes_chat._get_error_message(TimeoutError("LLM 无响应超过 30 秒")) == (
        "请求超时，请重新提问"
    )


def test_wrapped_timeout_maps_to_retryable_user_message():
    error = RuntimeError("Agent C LLM 调用失败")
    error.__cause__ = TimeoutError("sensitive upstream timeout detail")

    assert routes_chat._get_error_message(error) == "请求超时，请重新提问"
