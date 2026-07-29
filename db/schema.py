"""Alembic 迁移入口、旧库安全接管与启动前完整性门禁。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text, update

from db.database import Base, engine
from db.models import SchemaVersion


CURRENT_SCHEMA_VERSION = 3
SCHEMA_VERSION_ROW_ID = 1

INDEX_DEFINITIONS = (
    ("idx_chat_session", "chat_history", "CREATE INDEX idx_chat_session ON chat_history(user_id, session_id, created_at)"),
    ("idx_chat_user_time", "chat_history", "CREATE INDEX idx_chat_user_time ON chat_history(user_id, created_at)"),
    ("idx_chat_source_message", "chat_sources", "CREATE INDEX idx_chat_source_message ON chat_sources(message_id, position)"),
    ("idx_chat_turn_session", "chat_turns", "CREATE INDEX idx_chat_turn_session ON chat_turns(user_id, session_id, created_at)"),
    ("idx_chat_turn_status", "chat_turns", "CREATE INDEX idx_chat_turn_status ON chat_turns(status)"),
    ("idx_chat_turn_lease", "chat_turns", "CREATE INDEX idx_chat_turn_lease ON chat_turns(status, lease_expires_at)"),
    ("idx_episodic_dedup", "episodic_memory", "CREATE INDEX idx_episodic_dedup ON episodic_memory(user_id, event_type, created_at)"),
    ("idx_episodic_user_time", "episodic_memory", "CREATE INDEX idx_episodic_user_time ON episodic_memory(user_id, created_at)"),
    ("idx_semantic_chroma", "semantic_memory", "CREATE INDEX idx_semantic_chroma ON semantic_memory(chroma_id)"),
    ("idx_semantic_user", "semantic_memory", "CREATE INDEX idx_semantic_user ON semantic_memory(user_id)"),
    ("idx_doc_hash", "documents", "CREATE UNIQUE INDEX idx_doc_hash ON documents(user_id, content_hash)"),
    ("idx_session_user", "sessions", "CREATE INDEX idx_session_user ON sessions(user_id, status, last_active)"),
    ("idx_session_summary", "session_summary", "CREATE UNIQUE INDEX idx_session_summary ON session_summary(session_id)"),
)
INDEX_SQL = [statement for _, _, statement in INDEX_DEFINITIONS]

REQUIRED_COLUMNS = {
    "chat_turns": {"lease_owner", "lease_expires_at"},
}

CHAT_TURN_FOREIGN_KEY_DEFINITIONS = (
    (
        ("user_id",),
        "users",
        ("id",),
        "fk_chat_turn_user",
        "FOREIGN KEY (user_id) REFERENCES users(id)",
    ),
    (
        ("session_id",),
        "sessions",
        ("id",),
        "fk_chat_turn_session",
        "FOREIGN KEY (session_id) REFERENCES sessions(id)",
    ),
    (
        ("user_message_id",),
        "chat_history",
        ("id",),
        "fk_chat_turn_user_message",
        "FOREIGN KEY (user_message_id) REFERENCES chat_history(id) ON DELETE SET NULL",
    ),
    (
        ("assistant_message_id",),
        "chat_history",
        ("id",),
        "fk_chat_turn_assistant_message",
        "FOREIGN KEY (assistant_message_id) REFERENCES chat_history(id) ON DELETE SET NULL",
    ),
)


class SchemaNotReadyError(RuntimeError):
    """数据库结构与当前应用版本不匹配。"""


def _upgrade_instruction(detail: str) -> SchemaNotReadyError:
    return SchemaNotReadyError(
        f"数据库结构未就绪（{detail}）。请先停止服务并运行 "
        "`python -m db.init_db --upgrade`，再重新启动应用。"
    )


def _read_schema_version(bind) -> int | None:
    with bind.connect() as connection:
        return connection.execute(
            select(SchemaVersion.version).where(SchemaVersion.id == SCHEMA_VERSION_ROW_ID)
        ).scalar_one_or_none()


def _ensure_indexes(bind) -> None:
    with bind.begin() as connection:
        inspector = inspect(connection)
        existing_by_table: dict[str, set[str]] = {}
        for index_name, table_name, statement in INDEX_DEFINITIONS:
            existing = existing_by_table.setdefault(
                table_name,
                {item["name"] for item in inspector.get_indexes(table_name)},
            )
            if index_name in existing:
                continue
            connection.execute(text(statement))
            existing.add(index_name)


def _ensure_chat_turn_lease_columns(bind) -> None:
    """显式补齐 v3 列；SQLAlchemy create_all 不会修改已存在的表。"""
    inspector = inspect(bind)
    if "chat_turns" not in inspector.get_table_names():
        return

    existing = {item["name"] for item in inspector.get_columns("chat_turns")}
    statements = []
    if "lease_owner" not in existing:
        statements.append("ALTER TABLE chat_turns ADD COLUMN lease_owner VARCHAR(36) NULL")
    if "lease_expires_at" not in existing:
        statements.append("ALTER TABLE chat_turns ADD COLUMN lease_expires_at DATETIME NULL")

    if not statements:
        return
    with bind.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _chat_turn_foreign_key_signatures(inspector) -> set[tuple]:
    return {
        (
            tuple(item.get("constrained_columns") or ()),
            item.get("referred_table"),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys("chat_turns")
    }


def _ensure_chat_turn_foreign_keys(bind) -> None:
    """补齐 ChatTurn 权威归属外键；孤儿 reservation 可安全丢弃。"""
    inspector = inspect(bind)
    if "chat_turns" not in inspector.get_table_names():
        return
    existing = _chat_turn_foreign_key_signatures(inspector)
    missing = [
        definition
        for definition in CHAT_TURN_FOREIGN_KEY_DEFINITIONS
        if definition[:3] not in existing
    ]
    if not missing:
        return
    if bind.dialect.name != "mysql":
        raise SchemaNotReadyError(
            "chat_turns 缺少必需外键，当前数据库不支持原地补齐；请重建测试数据库"
        )

    with bind.begin() as connection:
        # ChatTurn 是幂等 reservation 派生状态；没有所属用户/会话的记录无法恢复且不能保留。
        connection.execute(text("""
            DELETE chat_turns
            FROM chat_turns
            LEFT JOIN users ON users.id = chat_turns.user_id
            LEFT JOIN sessions ON sessions.id = chat_turns.session_id
            WHERE users.id IS NULL OR sessions.id IS NULL
        """))
        connection.execute(text("""
            UPDATE chat_turns
            LEFT JOIN chat_history ON chat_history.id = chat_turns.user_message_id
            SET chat_turns.user_message_id = NULL
            WHERE chat_turns.user_message_id IS NOT NULL AND chat_history.id IS NULL
        """))
        connection.execute(text("""
            UPDATE chat_turns
            LEFT JOIN chat_history ON chat_history.id = chat_turns.assistant_message_id
            SET chat_turns.assistant_message_id = NULL
            WHERE chat_turns.assistant_message_id IS NOT NULL AND chat_history.id IS NULL
        """))
        for _, _, _, constraint_name, clause in missing:
            connection.execute(text(
                f"ALTER TABLE chat_turns ADD CONSTRAINT {constraint_name} {clause}"
            ))


def _record_current_version(bind) -> None:
    with bind.begin() as connection:
        current = connection.execute(
            select(SchemaVersion.version).where(SchemaVersion.id == SCHEMA_VERSION_ROW_ID)
        ).scalar_one_or_none()
        if current is None:
            connection.execute(
                SchemaVersion.__table__.insert().values(
                    id=SCHEMA_VERSION_ROW_ID,
                    version=CURRENT_SCHEMA_VERSION,
                )
            )
        elif current > CURRENT_SCHEMA_VERSION:
            raise SchemaNotReadyError(
                f"数据库结构版本 {current} 高于当前应用支持的 {CURRENT_SCHEMA_VERSION}，"
                "请部署匹配或更新版本的应用。"
            )
        elif current < CURRENT_SCHEMA_VERSION:
            connection.execute(
                update(SchemaVersion)
                .where(SchemaVersion.id == SCHEMA_VERSION_ROW_ID)
                .values(version=CURRENT_SCHEMA_VERSION)
            )


def _upgrade_legacy_schema(bind=engine) -> None:
    """补齐 additive 结构并记录版本；权威业务数据不改写，孤儿 reservation 可清理。"""
    table_names = set(inspect(bind).get_table_names())
    if "schema_version" in table_names:
        existing_version = _read_schema_version(bind)
        if existing_version is not None and existing_version > CURRENT_SCHEMA_VERSION:
            raise SchemaNotReadyError(
                f"数据库结构版本 {existing_version} 高于当前应用支持的 {CURRENT_SCHEMA_VERSION}，"
                "拒绝自动降级。"
            )

    Base.metadata.create_all(bind)
    _ensure_chat_turn_lease_columns(bind)
    _ensure_chat_turn_foreign_keys(bind)
    _ensure_indexes(bind)
    _record_current_version(bind)
    _assert_legacy_schema_ready(bind)


def _assert_legacy_schema_ready(bind=engine) -> None:
    """启动前验证当前版本所需的表、列、外键、索引和版本标记。"""
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    missing_tables = sorted(set(Base.metadata.tables) - table_names)
    if missing_tables:
        raise _upgrade_instruction(f"缺少表：{', '.join(missing_tables)}")

    missing_columns = []
    for table_name, required in REQUIRED_COLUMNS.items():
        existing = {item["name"] for item in inspector.get_columns(table_name)}
        missing_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in sorted(required - existing)
        )
    if missing_columns:
        raise _upgrade_instruction(f"缺少列：{', '.join(missing_columns)}")

    existing_foreign_keys = _chat_turn_foreign_key_signatures(inspector)
    missing_foreign_keys = [
        f"chat_turns.{columns[0]} -> {referred_table}.{referred_columns[0]}"
        for columns, referred_table, referred_columns, _, _ in CHAT_TURN_FOREIGN_KEY_DEFINITIONS
        if (columns, referred_table, referred_columns) not in existing_foreign_keys
    ]
    if missing_foreign_keys:
        raise _upgrade_instruction(f"缺少外键：{', '.join(missing_foreign_keys)}")

    version = _read_schema_version(bind)
    if version != CURRENT_SCHEMA_VERSION:
        if version is not None and version > CURRENT_SCHEMA_VERSION:
            raise SchemaNotReadyError(
                f"数据库结构版本 {version} 高于当前应用支持的 {CURRENT_SCHEMA_VERSION}，"
                "请部署匹配或更新版本的应用。"
            )
        raise _upgrade_instruction(
            f"当前版本标记为 {version!r}，应用要求 {CURRENT_SCHEMA_VERSION}"
        )

    missing_indexes = []
    indexes_by_table: dict[str, set[str]] = {}
    for index_name, table_name, _ in INDEX_DEFINITIONS:
        existing = indexes_by_table.setdefault(
            table_name,
            {item["name"] for item in inspector.get_indexes(table_name)},
        )
        if index_name not in existing:
            missing_indexes.append(index_name)
    if missing_indexes:
        raise _upgrade_instruction(f"缺少索引：{', '.join(sorted(missing_indexes))}")


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(connection=None) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _alembic_head() -> str:
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    if head is None:
        raise SchemaNotReadyError("Alembic 未定义 head revision")
    return head


def _current_alembic_revision(bind) -> str | None:
    with bind.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _run_alembic(bind, action, revision: str) -> None:
    with bind.begin() as connection:
        action(_alembic_config(connection), revision)


def upgrade_schema(bind=engine) -> None:
    """升级到 Alembic head；未版本化旧库先经旧门禁验证后再接管。"""
    table_names = set(inspect(bind).get_table_names())
    business_tables = table_names - {"alembic_version"}

    if business_tables and "alembic_version" not in table_names:
        # 先执行原有的安全增量升级，再做完整结构检查。只有检查通过才允许 stamp，
        # 防止残缺库被误标为最新版本。
        _upgrade_legacy_schema(bind)
        _assert_legacy_schema_ready(bind)
        _run_alembic(bind, command.stamp, "0001_current_schema")

    _run_alembic(bind, command.upgrade, "head")
    assert_schema_ready(bind)


def downgrade_schema(bind=engine, revision: str = "-1") -> None:
    """显式执行 Alembic 降级；仅供运维或迁移测试调用。"""
    _run_alembic(bind, command.downgrade, revision)


def assert_schema_ready(bind=engine) -> None:
    """验证业务结构完整且 Alembic revision 与代码 head 一致。"""
    _assert_legacy_schema_ready(bind)
    current = _current_alembic_revision(bind)
    head = _alembic_head()
    if current != head:
        raise _upgrade_instruction(
            f"Alembic 当前版本为 {current!r}，应用要求 {head!r}"
        )
