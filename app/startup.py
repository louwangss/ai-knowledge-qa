"""应用启动时必须完成的轻量业务自举。"""

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chat_turn_lease import get_database_utc_now
from config import APP_USER_ID
from db.database import SessionLocal
from db.models import ChatTurn, User


def mark_interrupted_chat_turns_failed(db: Session) -> int:
    """回收没有租约或租约已过期的 processing turn，不影响其他在线 worker。"""
    try:
        database_now = get_database_utc_now(db)
        updated = db.query(ChatTurn).filter(
            ChatTurn.status == "processing",
            or_(
                ChatTurn.lease_owner.is_(None),
                ChatTurn.lease_expires_at.is_(None),
                ChatTurn.lease_expires_at <= database_now,
            ),
        ).update(
            {
                ChatTurn.status: "failed",
                ChatTurn.lease_owner: None,
                ChatTurn.lease_expires_at: None,
                ChatTurn.updated_at: database_now,
            },
            synchronize_session=False,
        )
        db.commit()
        return updated
    except Exception:
        db.rollback()
        raise


def recover_interrupted_chat_turns() -> int:
    """使用独立 Session 执行启动恢复，并始终释放连接。"""
    db = SessionLocal()
    try:
        return mark_interrupted_chat_turns_failed(db)
    finally:
        db.close()


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
