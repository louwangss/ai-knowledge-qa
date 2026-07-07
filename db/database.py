"""SQLAlchemy 引擎 + Session 工厂"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from config import MYSQL_URL

engine = create_engine(MYSQL_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：获取 DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
