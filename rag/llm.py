"""LLM 配置：DeepSeek"""
from typing import TYPE_CHECKING

from config import DEEPSEEK_API_KEY, LLM_MODEL, LLM_BASE_URL

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI


def get_llm(
    temperature: float = 0.3,
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> "ChatOpenAI":
    """首次实际问答或摘要时再加载 LLM SDK。"""
    from langchain_openai import ChatOpenAI

    kwargs = {
        "model": LLM_MODEL,
        "api_key": DEEPSEEK_API_KEY,
        "base_url": LLM_BASE_URL,
        "temperature": temperature,
        "streaming": True,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return ChatOpenAI(**kwargs)
