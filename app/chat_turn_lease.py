"""ChatTurn 租约的数据库时钟与状态判断。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, func, select
from sqlalchemy.orm import Session


def get_database_utc_now(db: Session) -> datetime:
    """读取数据库时钟，避免多 worker 主机时钟偏差影响租约判断。"""
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "mysql":
        clock = func.utc_timestamp(type_=DateTime())
    else:
        clock = func.current_timestamp()

    value = db.execute(select(clock)).scalar_one()
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def new_lease_expiry(db: Session, lease_seconds: int) -> datetime:
    return get_database_utc_now(db) + timedelta(seconds=lease_seconds)


def has_live_lease(turn, database_now: datetime) -> bool:
    return bool(
        turn.lease_owner
        and turn.lease_expires_at
        and turn.lease_expires_at > database_now
    )
