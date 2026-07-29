"""统一错误处理"""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.observability import log_event

logger = logging.getLogger(__name__)


async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


async def generic_error_handler(request: Request, exc: Exception):
    route = getattr(request.scope.get("route"), "path", request.url.path)
    request_id = request.scope.get("request_id")
    log_event(
        logger,
        "request_failed",
        level=logging.ERROR,
        request_id=request_id,
        route=route,
        error_type=type(exc).__name__,
    )
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误"},
        headers=headers,
    )
