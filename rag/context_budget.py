"""按优先级为动态 LLM 上下文分配统一字符预算。"""


def bound_context_sections(
    sections: list[tuple[str, str]],
    *,
    max_chars: int,
) -> dict[str, str]:
    """依输入顺序分配预算；高优先级资料先保留，任何结果都不越界。"""
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正数")
    remaining = max_chars
    bounded: dict[str, str] = {}
    for name, value in sections:
        text = value if isinstance(value, str) else str(value)
        bounded[name] = text[:remaining]
        remaining -= len(bounded[name])
    return bounded
