"""pytest 公共配置：测试环境变量 + fixtures

必须在任何项目模块导入之前设置环境变量，
否则 config.py 的必填校验会 RuntimeError。
"""
import os
import sys
from pathlib import Path

# 在导入项目模块之前设置测试环境变量
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test_db")
os.environ.setdefault("REDIS_PASSWORD", "")
# 认证测试使用固定身份；不能继承 CI/开发环境值，否则硬编码测试请求会产生 401/403。
os.environ["APP_ACCESS_TOKEN"] = "test-access-token"
os.environ["APP_USER_ID"] = "u1"

# 项目根目录加入 path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


import pytest


@pytest.fixture(autouse=True)
def isolate_real_startup_database(monkeypatch):
    """TestClient 进入 lifespan 时不连接开发者的真实 MySQL。"""
    monkeypatch.setattr("app.main.assert_schema_ready", lambda: None)
    monkeypatch.setattr("app.main.recover_interrupted_chat_turns", lambda: None)
    monkeypatch.setattr("app.main.initialize_app_user", lambda: None)
