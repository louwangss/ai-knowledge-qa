"""LLM 配置：DeepSeek"""
import os

from langchain_openai import ChatOpenAI

from config import DEEPSEEK_API_KEY, LLM_MODEL, LLM_BASE_URL


def get_llm(temperature: float = 0.3) -> ChatOpenAI:
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=LLM_BASE_URL,
        temperature=temperature,
        streaming=True,
    )
