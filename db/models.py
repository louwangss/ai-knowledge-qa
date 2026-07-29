"""ORM 模型定义"""
from datetime import datetime
from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, TIMESTAMP, UniqueConstraint
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
    index_version = Column(Integer, nullable=False, default=1)
    indexed_version = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    user = relationship("User", back_populates="documents")


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    mode = Column(String(20))  # normal / deep
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    user = relationship("User", back_populates="chat_histories")
    session = relationship("Session", back_populates="chat_histories")
    sources = relationship(
        "ChatSource",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="ChatSource.position",
    )


class ChatSource(Base):
    """回答引用的结构化来源；chat_history 保持原表结构不变。"""
    __tablename__ = "chat_sources"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    message_id = Column(
        BigInteger,
        ForeignKey("chat_history.id", ondelete="CASCADE"),
        nullable=False,
    )
    source = Column(String(500), nullable=False)
    score = Column(Float, nullable=False, default=0)
    position = Column(Integer, nullable=False, default=0)

    message = relationship("ChatHistory", back_populates="sources")


class ChatTurn(Base):
    """客户端幂等键对应的一次问答状态；最终消息仍存放在 chat_history。"""
    __tablename__ = "chat_turns"

    client_turn_id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="processing")
    user_message_id = Column(
        BigInteger,
        ForeignKey("chat_history.id", ondelete="SET NULL"),
        nullable=True,
    )
    assistant_message_id = Column(
        BigInteger,
        ForeignKey("chat_history.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 每次执行尝试使用新的 owner 作为 fencing token；过期后其他 worker 可安全接管。
    lease_owner = Column(String(36), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)


class SchemaVersion(Base):
    """应用数据库结构版本；固定使用 id=1 的单行记录。"""
    __tablename__ = "schema_version"

    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)


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
    index_version = Column(Integer, nullable=False, default=1)
    indexed_version = Column(Integer, nullable=False, default=0)
    index_state = Column(String(20), nullable=False, default="pending")
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="semantic_memories")


class IndexJob(Base):
    """MySQL 中的持久化索引任务；Chroma 仅保存可重建的派生状态。"""
    __tablename__ = "index_jobs"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "operation",
            "desired_version",
            name="uq_index_job_target_version",
        ),
        Index("idx_index_job_available", "status", "next_attempt_at", "lease_expires_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    entity_type = Column(String(20), nullable=False)
    entity_id = Column(String(64), nullable=False)
    operation = Column(String(20), nullable=False)
    desired_version = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    lease_owner = Column(String(36), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_error_type = Column(String(100), nullable=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)


class SessionSummary(Base):
    """会话级摘要持久化（与 sessions 一对一）"""
    __tablename__ = "session_summary"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False, unique=True)
    summary = Column(Text, nullable=False)
    compressed_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("Session", backref="summary_record")
