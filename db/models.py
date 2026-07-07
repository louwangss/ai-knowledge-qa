"""ORM 模型定义"""
from datetime import datetime
from sqlalchemy import Column, String, Text, BigInteger, Integer, TIMESTAMP, ForeignKey
from sqlalchemy.orm import relationship

from db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    username = Column(String(50), nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    sessions = relationship("Session", back_populates="user")
    documents = relationship("Document", back_populates="user")
    chat_histories = relationship("ChatHistory", back_populates="user")
    episodic_memories = relationship("EpisodicMemory", back_populates="user")
    semantic_memories = relationship("SemanticMemory", back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    title = Column(String(100))
    status = Column(String(20), default="active")
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    last_active = Column(TIMESTAMP, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")
    chat_histories = relationship("ChatHistory", back_populates="session")
    episodic_memories = relationship("EpisodicMemory", back_populates="session")


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(20), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(BigInteger)
    chunk_count = Column(Integer)
    chunk_size = Column(Integer, nullable=False)
    chunk_overlap = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(20), default="processing")
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    user = relationship("User", back_populates="documents")


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    mode = Column(String(20))  # normal / deep，仅 user 消息记录
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    user = relationship("User", back_populates="chat_histories")
    session = relationship("Session", back_populates="chat_histories")


class EpisodicMemory(Base):
    __tablename__ = "episodic_memory"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    event_type = Column(String(30), nullable=False)
    content = Column(Text, nullable=False)
    question_text = Column(Text)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    user = relationship("User", back_populates="episodic_memories")
    session = relationship("Session", back_populates="episodic_memories")


class SemanticMemory(Base):
    __tablename__ = "semantic_memory"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    concept = Column(String(100))
    content = Column(Text, nullable=False)
    chroma_id = Column(String(100))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="semantic_memories")


class SessionSummary(Base):
    """会话级摘要持久化（与 sessions 一对一）"""
    __tablename__ = "session_summary"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False, unique=True)
    summary = Column(Text, nullable=False)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("Session", backref="summary_record")
