"""Agent A：拆解问题为 3~5 个子问题"""
from agents.state import ResearchState
from rag.llm import get_llm

PROMPT = """你是一个问题分析专家。请将用户的问题拆解为 3~5 个子问题或搜索关键词，用于后续检索。

要求：
1. 每个子问题 10~20 字，简洁明确
2. 覆盖问题的不同方面
3. 输出 JSON 数组格式

示例：
用户问题：什么是RAG以及它如何减少大模型幻觉？
输出：["RAG的定义和核心原理", "RAG的工作流程", "大模型幻觉问题的成因", "RAG如何缓解幻觉问题"]

用户问题：{question}

请直接输出 JSON 数组，不要加其他说明："""


def agent_a_decompose(state: ResearchState) -> dict:
    """拆解问题"""
    question = state["original_question"]
    llm = get_llm(temperature=0.3)

    resp = llm.invoke(PROMPT.format(question=question))

    # 解析 JSON 数组
    import json
    import re

    text = resp.content.strip()
    # 尝试提取 JSON 数组
    match = re.search(r'\[.*?\]', text, re.DOTALL)
    if match:
        sub_questions = json.loads(match.group())
    else:
        # fallback：按换行分割
        lines = [line.strip().strip("0123456789.、- ") for line in text.split("\n") if line.strip()]
        sub_questions = lines[:5]

    if not sub_questions:
        sub_questions = [question]

    return {"sub_questions": sub_questions}
