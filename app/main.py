"""FastAPI 入口：路由注册 + lifespan 后台补偿任务"""
import asyncio
import logging
import os
import sys
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes_sessions import router as sessions_router
from app.api.routes_documents import router as documents_router
from app.api.routes_notes import router as notes_router
from app.api.routes_chat import router as chat_router
from app.api.routes_web import router as web_router
from app.deps import require_api_access
from app.error_handler import value_error_handler, generic_error_handler
from app.observability import RequestObservabilityMiddleware
from app.security_headers import SecurityHeadersMiddleware
from app.startup import (
    initialize_app_user,
    recover_interrupted_chat_turns,
)
from config import API_HOST
from db.schema import assert_schema_ready

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _compensation_loop():
    """后台补偿：每 5 分钟扫描 chroma_id IS NULL 的笔记"""
    while True:
        await asyncio.sleep(300)
        try:
            from db.database import SessionLocal
            from memory.semantic import compensation_task
            db = SessionLocal()
            try:
                compensation_task(db)
            finally:
                db.close()
        except Exception as e:
            logger.error("补偿任务失败: error_type=%s", type(e).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(assert_schema_ready)
    await asyncio.to_thread(recover_interrupted_chat_turns)
    await asyncio.to_thread(initialize_app_user)
    task = asyncio.create_task(_compensation_loop())
    logger.info("FastAPI 启动，后台补偿任务已启动")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        logger.info("FastAPI 关闭")


app = FastAPI(title="AI 知识库问答系统", lifespan=lifespan)
app.add_middleware(RequestObservabilityMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# 注册路由
_protected_dependencies = [Depends(require_api_access)]
app.include_router(sessions_router, dependencies=_protected_dependencies)
app.include_router(documents_router, dependencies=_protected_dependencies)
app.include_router(notes_router, dependencies=_protected_dependencies)
app.include_router(chat_router, dependencies=_protected_dependencies)
app.include_router(web_router)

# 错误处理
app.add_exception_handler(ValueError, value_error_handler)
app.add_exception_handler(Exception, generic_error_handler)

# 前端构建存在时提供同源静态托管；开发模式仍由 Vite 代理 API。
WEB_DIST_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"
if WEB_DIST_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=WEB_DIST_DIR, html=True), name="react-web")


@app.get("/")
def root():
    return {"status": "ok", "service": "AI Knowledge QA"}


def run_api():
    """使用配置的监听地址启动开发 API 服务。"""
    import uvicorn

    uvicorn.run("app.main:app", host=API_HOST, port=8000, reload=True)


if __name__ == "__main__":
    run_api()
