"""FastAPI 入口：路由注册 + lifespan 后台补偿任务"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI

from app.api.routes_users import router as users_router
from app.api.routes_sessions import router as sessions_router
from app.api.routes_documents import router as documents_router
from app.api.routes_notes import router as notes_router
from app.api.routes_chat import router as chat_router
from app.deps import require_access_token
from app.error_handler import value_error_handler, generic_error_handler
from app.observability import RequestObservabilityMiddleware
from config import API_HOST

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
    task = asyncio.create_task(_compensation_loop())
    logger.info("FastAPI 启动，后台补偿任务已启动")
    yield
    task.cancel()
    logger.info("FastAPI 关闭")


app = FastAPI(title="AI 知识库问答系统", lifespan=lifespan)
app.add_middleware(RequestObservabilityMiddleware)

# 注册路由
_protected_dependencies = [Depends(require_access_token)]
app.include_router(users_router, dependencies=_protected_dependencies)
app.include_router(sessions_router, dependencies=_protected_dependencies)
app.include_router(documents_router, dependencies=_protected_dependencies)
app.include_router(notes_router, dependencies=_protected_dependencies)
app.include_router(chat_router, dependencies=_protected_dependencies)

# 错误处理
app.add_exception_handler(ValueError, value_error_handler)
app.add_exception_handler(Exception, generic_error_handler)


@app.get("/")
def root():
    return {"status": "ok", "service": "AI Knowledge QA"}


def run_api():
    """使用配置的监听地址启动开发 API 服务。"""
    import uvicorn

    uvicorn.run("app.main:app", host=API_HOST, port=8000, reload=True)


if __name__ == "__main__":
    run_api()
