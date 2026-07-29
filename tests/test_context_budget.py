"""送入 LLM 的动态上下文必须有统一字符预算。"""

from rag.context_budget import bound_context_sections


def test_context_budget_preserves_priority_and_never_exceeds_limit():
    bounded = bound_context_sections(
        [("documents", "A" * 8), ("notes", "B" * 8), ("web", "C" * 8)],
        max_chars=12,
    )

    assert bounded["documents"] == "A" * 8
    assert bounded["notes"] == "B" * 4
    assert bounded["web"] == ""
    assert sum(len(value) for value in bounded.values()) == 12
