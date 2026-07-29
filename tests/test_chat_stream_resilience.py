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


def test_normal_agent_failure_after_first_token_does_not_fallback(monkeypatch):
    """Agent 已向客户端输出内容后失败，不能再拼接一份普通 LLM 答案。"""
    import config

    fallback_starts = []

    class FallbackLlm:
        async def astream(self, prompt):
            fallback_starts.append(prompt)
            yield SimpleNamespace(content="普通模型的第二份答案")

    async def broken_agent_stream(prompt):
        yield {"type": "token", "content": "Agent 的半截答案"}
        raise RuntimeError("agent stream failed")

    monkeypatch.setattr(config, "TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(routes_chat, "get_llm", lambda **kwargs: FallbackLlm())
    monkeypatch.setattr(routes_chat, "_agent_stream", broken_agent_stream)

    async def run():
        events = []
        error = None
        try:
            async for event in routes_chat._stream_normal(_normal_payload(), EMPTY_CONTEXT):
                events.append(event)
        except Exception as exc:  # noqa: BLE001 - 测试需要观察对外传播的原始失败类型
            error = exc
        return events, error

    events, error = asyncio.run(run())

    assert isinstance(error, RuntimeError)
    assert [event["content"] for event in events if event["type"] == "token"] == [
        "Agent 的半截答案"
    ]
    assert fallback_starts == []


def test_normal_agent_failure_before_first_token_allows_fallback(monkeypatch):
    """Agent 尚未输出 token 时失败，可以安全切换到普通 LLM。"""
    import config

    fallback_starts = []

    class FallbackLlm:
        async def astream(self, prompt):
            fallback_starts.append(prompt)
            yield SimpleNamespace(content="普通模型的完整答案")

    async def broken_agent_stream(prompt):
        yield {"type": "status", "content": "正在调用工具"}
        raise RuntimeError("agent failed before answer")

    monkeypatch.setattr(config, "TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(routes_chat, "get_llm", lambda **kwargs: FallbackLlm())
    monkeypatch.setattr(routes_chat, "_agent_stream", broken_agent_stream)

    async def run():
        return [
            event
            async for event in routes_chat._stream_normal(_normal_payload(), EMPTY_CONTEXT)
        ]

    events = asyncio.run(run())

    assert [event["content"] for event in events if event["type"] == "token"] == [
        "普通模型的完整答案"
    ]
    assert len(fallback_starts) == 1


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


def test_normal_agent_output_uses_idle_timeout(monkeypatch):
    """Agent 长时间没有对外进度时必须抛出项目级 idle timeout。"""
    import config

    class UnusedFallbackLlm:
        async def astream(self, prompt):
            if False:  # pragma: no cover - Agent 已输出 token 后不能 fallback
                yield None

    async def hanging_agent_stream(prompt):
        yield {"type": "token", "content": "Agent 已开始回答"}
        await asyncio.Event().wait()

    monkeypatch.setattr(config, "TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(routes_chat, "CHAT_STAGE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(routes_chat, "get_llm", lambda **kwargs: UnusedFallbackLlm())
    monkeypatch.setattr(routes_chat, "_agent_stream", hanging_agent_stream)

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
