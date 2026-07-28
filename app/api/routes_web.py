"""独立 Web 前端的本机会话入口。"""

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from app.web_auth import (
    WEB_SESSION_COOKIE,
    exchange_web_credential,
    has_valid_web_session,
    is_loopback_client,
    require_allowed_web_origin,
)
from app.deps import require_api_access
from config import APP_USER_ID


router = APIRouter(prefix="/api/v1/web", tags=["web"])


class WebSessionCreate(BaseModel):
    token: str = Field(min_length=1, max_length=512)


@router.get("/session/status")
def get_web_session_status(request: Request):
    return {"authenticated": has_valid_web_session(request)}


@router.get("/config", dependencies=[Depends(require_api_access)])
def get_web_config():
    return {"user_id": APP_USER_ID}


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
def create_web_session(payload: WebSessionCreate, request: Request):
    if not is_loopback_client(request):
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅允许本机访问")
    require_allowed_web_origin(request)
    session_value = exchange_web_credential(payload.token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        key=WEB_SESSION_COOKIE,
        value=session_value,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/api/v1",
    )
    return response
