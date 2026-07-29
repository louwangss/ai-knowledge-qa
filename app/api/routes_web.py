"""独立 Web 前端的本机会话入口。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.web_auth import (
    WEB_SESSION_COOKIE,
    clear_web_login_failures,
    exchange_web_credential,
    has_valid_web_session,
    is_loopback_client,
    record_web_login_failure,
    require_allowed_web_origin,
    require_web_login_capacity,
    revoke_web_session,
)
from app.deps import require_api_access
from config import APP_USER_ID, WEB_SESSION_TTL_SECONDS


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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅允许本机访问")
    require_allowed_web_origin(request)
    require_web_login_capacity(request)
    try:
        session_value = exchange_web_credential(payload.token)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            record_web_login_failure(request)
        raise
    clear_web_login_failures(request)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        key=WEB_SESSION_COOKIE,
        value=session_value,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/api/v1",
        max_age=WEB_SESSION_TTL_SECONDS,
    )
    return response


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def delete_web_session(request: Request):
    if not is_loopback_client(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅允许本机访问")
    require_allowed_web_origin(request)
    revoke_web_session(request)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        key=WEB_SESSION_COOKIE,
        value="",
        max_age=0,
        expires=0,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/api/v1",
    )
    return response
