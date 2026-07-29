"""Agent A：用受校验的 JSON 对象拆解问题。"""

import logging

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from agents.state import ResearchState
from config import CHAT_STAGE_TIMEOUT_SECONDS
from rag.llm import get_llm


logger = logging.getLogger(__name__)

PROMPT = """你是一个问题分析专家。请将用户问题拆解为 3~5 个用于知识库检索的子问题。

要求：
1. 覆盖问题的不同方面，不重复
2. 每项是非空字符串
3. 只能输出一个 JSON 对象，格式为 {{"sub_questions":["问题1","问题2","问题3"]}}

用户问题：{question}
"""


class Decomposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sub_questions: list[StrictStr] = Field(min_length=3, max_length=5)

    @field_validator("sub_questions")
    @classmethod
    def normalize_questions(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("子问题不能为空")
        if len(set(normalized)) != len(normalized):
            raise ValueError("子问题不能重复")
        return normalized


def agent_a_decompose(state: ResearchState) -> dict:
    """解析结构化拆题结果；任一调用或校验失败均确定性退回原问题。"""
    question = state["original_question"]
    try:
        llm = get_llm(
            temperature=0.3,
            timeout=CHAT_STAGE_TIMEOUT_SECONDS,
            max_retries=0,
        ).bind(response_format={"type": "json_object"})
        response = llm.invoke(PROMPT.format(question=question))
        parsed = Decomposition.model_validate_json(response.content)
        return {"sub_questions": parsed.sub_questions}
    except Exception as exc:
        logger.warning("Agent A 结构化拆题失败，回退原问题: error_type=%s", type(exc).__name__)
        return {"sub_questions": [question]}
