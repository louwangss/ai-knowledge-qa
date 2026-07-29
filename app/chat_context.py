"""问答上下文与来源的格式化辅助。"""

import math
from numbers import Real


# source 与已有 Document.file_path 一样属于路径/URL 展示字段，沿用 500 字符边界。
CHAT_SOURCE_MAX_LENGTH = 500
# deep 模式的设计边界是 3~5 个子问题、每题检索 3 段，因此最多保留 15 个来源。
CHAT_SOURCE_MAX_COUNT = 15


def format_docs(docs: list[dict]) -> str:
    if not docs:
        return "（无相关文档）"
    parts = []
    for document in docs:
        source = document.get("metadata", {}).get("source", "")
        parts.append(f"[来源: {source}]\n{document['content']}")
    return "\n\n".join(parts)


def format_notes(notes: list[dict]) -> str:
    if not notes:
        return "无"
    parts = []
    for note in notes:
        metadata = note.get("metadata", {})
        concept = metadata.get("concept", "")
        parts.append(f"[{concept}] {note['content']}")
    return "\n".join(parts)


def format_episodic(events: list[dict]) -> str:
    if not events:
        return "无"
    return "\n".join(event.get("content", "") for event in events)


def format_short_term(short_term: dict) -> str:
    parts = []
    if short_term.get("summary"):
        parts.append(f"[早期对话摘要]\n{short_term['summary']}")
    if short_term.get("messages"):
        messages = short_term["messages"]
        has_summary = bool(short_term.get("summary"))
        if has_summary:
            recent = [
                f"[早期对话之后第{index + 1}条] {message['role']}: {message['content'][:300]}"
                for index, message in enumerate(messages)
            ]
        else:
            recent = [
                f"[第{index + 1}条] {message['role']}: {message['content'][:300]}"
                for index, message in enumerate(messages)
            ]
        parts.append("[对话记录]\n" + "\n".join(recent))
    return "\n\n".join(parts) if parts else "无"


def format_sources(context: dict) -> list[dict]:
    sources = []
    for document in context.get("documents", []):
        metadata = document.get("metadata", {})
        sources.append(
            {
                "source": metadata.get("source", "unknown"),
                "score": document.get("score", 0),
            }
        )
    return sources


def normalize_sources(raw_sources: object) -> list[dict]:
    """将检索/工具输出收敛为可持久化、可安全序列化的稳定来源列表。"""
    if not isinstance(raw_sources, list):
        return []

    normalized = []
    seen = set()
    for item in raw_sources:
        if len(normalized) >= CHAT_SOURCE_MAX_COUNT:
            break
        if not isinstance(item, dict):
            continue
        raw_source = item.get("source")
        if not isinstance(raw_source, str):
            continue
        source = raw_source.strip()[:CHAT_SOURCE_MAX_LENGTH]
        if not source or source in seen:
            continue

        raw_score = item.get("score", 0)
        score = (
            float(raw_score)
            if isinstance(raw_score, Real) and not isinstance(raw_score, bool)
            else 0.0
        )
        if not math.isfinite(score):
            score = 0.0

        seen.add(source)
        normalized.append({"source": source, "score": score})
    return normalized
