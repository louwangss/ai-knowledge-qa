"""内部摘要 LLM 的超时、重试与失败安全性测试。"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_summary_llm_config_has_bounded_defaults():
    env = os.environ.copy()
    env.pop("SUMMARY_LLM_TIMEOUT_SECONDS", None)
    env.pop("SUMMARY_LLM_MAX_RETRIES", None)
    env["PYTHONUTF8"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import config; "
                "print(config.SUMMARY_LLM_TIMEOUT_SECONDS, "
                "config.SUMMARY_LLM_MAX_RETRIES)"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "30 0"


def test_summary_llm_config_accepts_positive_timeout_and_zero_retries():
    env = os.environ.copy()
    env["SUMMARY_LLM_TIMEOUT_SECONDS"] = "12"
    env["SUMMARY_LLM_MAX_RETRIES"] = "0"
    env["PYTHONUTF8"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import config; "
                "print(config.SUMMARY_LLM_TIMEOUT_SECONDS, "
                "config.SUMMARY_LLM_MAX_RETRIES)"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "12 0"


@pytest.mark.parametrize(
    ("name", "value", "expected_message"),
    [
        (
            "SUMMARY_LLM_TIMEOUT_SECONDS",
            "0",
            "环境变量 SUMMARY_LLM_TIMEOUT_SECONDS 必须为正整数",
        ),
        (
            "SUMMARY_LLM_MAX_RETRIES",
            "-1",
            "环境变量 SUMMARY_LLM_MAX_RETRIES 必须为非负整数",
        ),
        (
            "SUMMARY_LLM_TIMEOUT_SECONDS",
            "invalid",
            "环境变量 SUMMARY_LLM_TIMEOUT_SECONDS 必须为正整数",
        ),
        (
            "SUMMARY_LLM_MAX_RETRIES",
            "invalid",
            "环境变量 SUMMARY_LLM_MAX_RETRIES 必须为非负整数",
        ),
    ],
)
def test_summary_llm_config_rejects_out_of_range_values(name, value, expected_message):
    env = os.environ.copy()
    env[name] = value
    env["PYTHONUTF8"] = "1"

    completed = subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert expected_message in completed.stderr


def test_get_llm_only_passes_bounds_when_explicitly_configured():
    from rag.llm import get_llm

    with patch("langchain_openai.ChatOpenAI") as chat_openai:
        get_llm(temperature=0.0, timeout=12, max_retries=0)
        explicit_kwargs = chat_openai.call_args.kwargs

        chat_openai.reset_mock()
        get_llm(temperature=0.3)
        default_kwargs = chat_openai.call_args.kwargs

    assert explicit_kwargs["timeout"] == 12
    assert explicit_kwargs["max_retries"] == 0
    assert "timeout" not in default_kwargs
    assert "max_retries" not in default_kwargs


def test_generate_summary_uses_bounds_and_failure_keeps_old_summary_untouched(monkeypatch):
    from memory import short_term

    llm = MagicMock()
    llm.invoke.side_effect = TimeoutError("summary timeout")
    get_llm = MagicMock(return_value=llm)
    monkeypatch.setattr(short_term, "get_llm", get_llm)
    monkeypatch.setattr(short_term, "SUMMARY_LLM_TIMEOUT_SECONDS", 12)
    monkeypatch.setattr(short_term, "SUMMARY_LLM_MAX_RETRIES", 0)

    redis = MagicMock()
    redis.get.return_value = "已经持久化的旧摘要"
    redis.lrange.return_value = [
        '{"role": "user", "content": "需要压缩的新消息"}'
    ]
    db_session = MagicMock()

    short_term._do_compress(redis, "u1", "s1", db_session=db_session)

    get_llm.assert_called_once_with(
        temperature=0.0,
        timeout=12,
        max_retries=0,
    )
    redis.set.assert_not_called()
    redis.ltrim.assert_not_called()
    db_session.add.assert_not_called()
    db_session.commit.assert_not_called()
