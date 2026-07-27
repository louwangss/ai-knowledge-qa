"""用户路由：POST 获取或创建固定的单用户账号。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.schemas import UserCreate, UserResponse
from config import APP_USER_ID
from db.models import User

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.post("", response_model=UserResponse)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    # access token 已代表当前演示用户；刷新页面时复用同一固定 ID。
    existing = db.query(User).filter(User.id == APP_USER_ID).first()
    if existing:
        return existing
    user = User(
        id=APP_USER_ID,
        username=payload.username,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
