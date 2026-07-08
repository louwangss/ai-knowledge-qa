"""Agent C：整合所有上下文，生成结构化回答（流式输出）"""
import logging

from agents.state import ResearchState
from rag.llm import get_llm

logger = logging.getLogger(__name__)


def _format_short_term_memory(stm: dict) -> str:
    """格式化短期记忆为 prompt 字符串"""
    parts = []
    if stm and stm.get("summary"):
        parts.append(f"[早期对话摘要]\n{stm['summary']}")
    if stm and stm.get("messages"):
        recent_lines = []
        for msg in stm["messages"]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            recent_lines.append(f"{role}: {content}")
        parts.append("[最近对话]\n" + "\n".join(recent_lines))
    return "\n\n".join(parts) if parts else "无"


def _format_docs(docs: list) -> str:
    """按子问题分组组织文档上下文"""
    if not docs:
        return "（无检索到相关文档）"
    parts = []
    by_question: dict[str, list] = {}
    for doc in docs:
        sq = doc.get("sub_question", "综合")
        by_question.setdefault(sq, []).append(doc)

    for sq, ds in by_question.items():
        parts.append(f"### {sq}")
        for d in ds:
            parts.append(f"[来源: {d['source']}, 相关度: {d['score']:.2f}]\n{d['content']}")
    return "\n\n".join(parts)


def _format_notes(notes: list) -> str:
    if not notes:
        return "无"
    parts = []
    for n in notes:
        concept = n.get("concept", "")
        content = n.get("content", n.get("metadata", {}).get("content", ""))
        parts.append(f"[{concept}] {content}")
    return "\n".join(parts)


def _format_episodic(events: list) -> str:
    if not events:
        return "无"
    return "\n".join(e.get("content", "") for e in events)


def _build_prompt(state: ResearchState) -> str:
    docs = state.get("retrieved_docs", [])
    notes = state.get("notes", [])
    episodic = state.get("episodic_memory", [])
    stm = state.get("short_term_memory", "")
    question = state.get("original_question", "")

    docs_empty = len(docs) == 0

    # short_term_memory 可能是 str（已格式化）或 dict（原始）
    if isinstance(stm, dict):
        stm_text = _format_short_term_memory(stm)
    else:
        stm_text = stm if stm else "无"

    prompt = f"""你是一个知识库研究助手。请基于以下检索到的资料，对用户的问题进行结构化的深度分析。

# 检索到的文档资料
{_format_docs(docs)}

# 用户笔记
{_format_notes(notes)}

# 学习历程
{_format_episodic(episodic)}

# 对话上下文
{stm_text}

# 用户的问题
{question}

"""
    if docs_empty:
        prompt += """注意：当前文档库中未找到与该问题相关的内容。
请基于已有笔记和上下文回答，或建议用户上传相关文档。
不要编造文档中不存在的信息。
"""
    else:
        prompt += """要求：
1. 按主题结构化组织回答，层次分明，使用 Markdown 标题和列表
2. 引用文档内容时标注来源，格式：根据《来源名》...
3. 如果检索到的文档与问题无关，明确指出
4. 在回答的最后，对不同子问题的结论进行简要关联分析，指出它们之间的联系
5. 回答要准确、深入，不要编造信息
"""
    return prompt


def agent_c_summarize(state: ResearchState) -> dict:
    """生成最终回答，通过 custom stream 逐字输出"""
    from langgraph.config import get_stream_writer

    writer = get_stream_writer()
    llm = get_llm(temperature=0.3)
    prompt = _build_prompt(state)

    full_answer = ""
    try:
        for chunk in llm.stream(prompt):
            token = chunk.content
            if token:
                full_answer += token
                writer({"type": "token", "content": token})
    except Exception as e:
        logger.error(f"Agent C LLM 调用失败: {e}")
        full_answer = f"抱歉，生成回答时发生错误：{e}"

    return {"final_answer": full_answer}
