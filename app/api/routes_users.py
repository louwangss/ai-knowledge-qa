"""用户路由：POST 创建用户"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.schemas import UserCreate, UserResponse
from db.models import User

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.post("", response_model=UserResponse)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    # 用户名已存在则直接返回，避免刷新页面创建重复用户
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        return existing
    user = User(
        id=str(uuid.uuid4()),
        username=payload.username,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
