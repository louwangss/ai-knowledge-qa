"""联网规划隔离与 Tavily 结构化结果测试。"""

from types import SimpleNamespace

from tools import web_research


def test_planner_receives_only_current_question_and_date(monkeypatch):
    prompts = []

    class FakeLlm:
        def bind(self, **kwargs):
            assert kwargs["response_format"] == {"type": "json_object"}
            return self

        def invoke(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(content='{"needs_web":true,"query":"2026 current facts"}')

    monkeypatch.setattr(web_research, "get_llm", lambda **kwargs: FakeLlm())

    plan = web_research.plan_web_search("当前公开问题", "2026-07-29")

    assert plan.needs_web is True
    assert plan.query == "2026 current facts"
    assert "当前公开问题" in prompts[0]
    assert "私有文档" not in prompts[0]
    assert "历史消息" not in prompts[0]


def test_invalid_planner_output_disables_web_without_guessing(monkeypatch):
    class FakeLlm:
        def bind(self, **kwargs):
            return self

        def invoke(self, prompt):
            return SimpleNamespace(content="not json")

    monkeypatch.setattr(web_research, "get_llm", lambda **kwargs: FakeLlm())

    plan = web_research.plan_web_search("问题", "2026-07-29")

    assert plan.needs_web is False
    assert plan.query == ""


def test_structured_tavily_results_are_sanitized_without_literal_eval():
    results = web_research.normalize_web_results({
        "results": [
            {"url": "https://example.com/1", "title": "示例", "content": "正文", "score": 0.8},
            {"url": "javascript:alert(1)", "title": "危险", "content": "忽略"},
            {"title": "缺 URL", "content": "忽略"},
        ]
    })

    assert results == [{
        "url": "https://example.com/1",
        "title": "示例",
        "content": "正文",
        "source": "示例 - https://example.com/1",
        "score": 0.8,
    }]
