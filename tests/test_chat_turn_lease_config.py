"""ChatTurn 租约配置边界测试。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _import_config_with(**overrides):
    env = os.environ.copy()
    env.pop("CHAT_TURN_LEASE_SECONDS", None)
    env.pop("CHAT_TURN_HEARTBEAT_SECONDS", None)
    env.update(overrides)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import config; "
                "print(config.CHAT_TURN_LEASE_SECONDS, "
                "config.CHAT_TURN_HEARTBEAT_SECONDS)"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_chat_turn_lease_config_has_bounded_defaults():
    completed = _import_config_with()

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "90 30"


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        (
            {"CHAT_TURN_HEARTBEAT_SECONDS": "0"},
            "环境变量 CHAT_TURN_HEARTBEAT_SECONDS 必须为正整数",
        ),
        (
            {
                "CHAT_TURN_LEASE_SECONDS": "60",
                "CHAT_TURN_HEARTBEAT_SECONDS": "30",
            },
            "环境变量 CHAT_TURN_LEASE_SECONDS 必须至少为 CHAT_TURN_HEARTBEAT_SECONDS 的 3 倍",
        ),
    ],
)
def test_chat_turn_lease_config_rejects_unsafe_bounds(overrides, expected_message):
    completed = _import_config_with(**overrides)

    assert completed.returncode != 0
    assert expected_message in completed.stderr
