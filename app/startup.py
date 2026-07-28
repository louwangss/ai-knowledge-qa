"""应用启动时必须完成的轻量业务自举。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import APP_USER_ID
from db.database import SessionLocal
from db.models import User


def ensure_app_user(db: Session) -> User:
    """幂等创建配置指定的单用户，并兼容多进程同时启动。"""
    existing = db.get(User, APP_USER_ID)
    if existing is not None:
        return existing

    user = User(id=APP_USER_ID, username="user")
    db.add(user)
    try:
        db.commit()
        return user
    except IntegrityError:
        db.rollback()
        concurrent_user = db.get(User, APP_USER_ID)
        if concurrent_user is None:
            raise
        return concurrent_user


def initialize_app_user() -> None:
    """使用独立 Session 完成启动自举，并始终释放数据库连接。"""
    db = SessionLocal()
    try:
        ensure_app_user(db)
    finally:
        db.close()
