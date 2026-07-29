"""全局配置，从 .env 读取"""
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()

# --- 必填校验（放在 URL 构建之前） ---
_REQUIRED = [
    "DEEPSEEK_API_KEY",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_DATABASE",
    "APP_ACCESS_TOKEN",
]
for _key in _REQUIRED:
    if not os.getenv(_key):
        raise RuntimeError(f"环境变量 {_key} 未设置，请检查 .env 文件")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APP_ACCESS_TOKEN = os.getenv("APP_ACCESS_TOKEN", "").strip()
APP_USER_ID = os.getenv("APP_USER_ID", "default-user").strip()
API_HOST = os.getenv("API_HOST", "127.0.0.1").strip()
if not APP_ACCESS_TOKEN:
    raise RuntimeError("环境变量 APP_ACCESS_TOKEN 不能为空")
if not APP_USER_ID:
    raise RuntimeError("环境变量 APP_USER_ID 不能为空")
if len(APP_USER_ID) > 36:
    raise RuntimeError("环境变量 APP_USER_ID 长度不能超过 36")
if not API_HOST:
    raise RuntimeError("环境变量 API_HOST 不能为空")


def _positive_int_env(name: str, default: str) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须为正整数") from exc
    if value <= 0:
        raise RuntimeError(f"环境变量 {name} 必须为正整数")
    return value


def _non_negative_int_env(name: str, default: str) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须为非负整数") from exc
    if value < 0:
        raise RuntimeError(f"环境变量 {name} 必须为非负整数")
    return value


WEB_SESSION_TTL_SECONDS = _positive_int_env("APP_WEB_SESSION_TTL_SECONDS", "604800")
WEB_LOGIN_MAX_ATTEMPTS = _positive_int_env("APP_WEB_LOGIN_MAX_ATTEMPTS", "10")
WEB_LOGIN_WINDOW_SECONDS = _positive_int_env("APP_WEB_LOGIN_WINDOW_SECONDS", "300")

# ChatTurn 租约是可调的启发式边界：默认覆盖 3 个心跳周期，避免短暂调度抖动误判。
CHAT_TURN_LEASE_SECONDS = _positive_int_env("CHAT_TURN_LEASE_SECONDS", "90")
CHAT_TURN_HEARTBEAT_SECONDS = _positive_int_env("CHAT_TURN_HEARTBEAT_SECONDS", "30")
# 保留原普通问答 30 秒空闲边界，并统一约束检索完成时间与各流式阶段的无进度等待。
CHAT_STAGE_TIMEOUT_SECONDS = _positive_int_env("CHAT_STAGE_TIMEOUT_SECONDS", "30")
if CHAT_TURN_LEASE_SECONDS < CHAT_TURN_HEARTBEAT_SECONDS * 3:
    raise RuntimeError(
        "环境变量 CHAT_TURN_LEASE_SECONDS 必须至少为 "
        "CHAT_TURN_HEARTBEAT_SECONDS 的 3 倍"
    )

# Tavily 可选（不用 web_search 时不需要）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# --- MySQL ---
_mysql_host = os.getenv("MYSQL_HOST", "localhost")
_mysql_port = os.getenv("MYSQL_PORT", "3306")
_mysql_user = os.getenv("MYSQL_USER")
_mysql_password = quote_plus(os.getenv("MYSQL_PASSWORD"))
_mysql_db = os.getenv("MYSQL_DATABASE")
MYSQL_URL = f"mysql+pymysql://{_mysql_user}:{_mysql_password}@{_mysql_host}:{_mysql_port}/{_mysql_db}?charset=utf8mb4"

# --- Redis ---
_redis_host = os.getenv("REDIS_HOST", "localhost")
_redis_port = os.getenv("REDIS_PORT", "6379")
_redis_db = os.getenv("REDIS_DB", "0")
_redis_password = os.getenv("REDIS_PASSWORD", "")
_redis_auth = f":{quote_plus(_redis_password)}@" if _redis_password else ""
REDIS_URL = f"redis://{_redis_auth}{_redis_host}:{_redis_port}/{_redis_db}"

# --- 路径 ---
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/uploads")
try:
    MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
except ValueError as exc:
    raise RuntimeError("环境变量 MAX_UPLOAD_BYTES 必须为正整数") from exc
if MAX_UPLOAD_BYTES <= 0:
    raise RuntimeError("环境变量 MAX_UPLOAD_BYTES 必须为正整数")

# --- Embedding 模型 ---
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

# --- LLM ---
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
SUMMARY_LLM_TIMEOUT_SECONDS = _positive_int_env("SUMMARY_LLM_TIMEOUT_SECONDS", "30")
SUMMARY_LLM_MAX_RETRIES = _non_negative_int_env("SUMMARY_LLM_MAX_RETRIES", "0")

# --- RAG 检索 ---
RAG_RELEVANCE_THRESHOLD = float(os.getenv("RAG_RELEVANCE_THRESHOLD", "0.5"))
