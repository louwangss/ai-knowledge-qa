"""Alembic 迁移入口与旧库接管行为。"""

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.schema import assert_schema_ready, downgrade_schema, upgrade_schema
from db.schema import SchemaNotReadyError


ROOT = Path(__file__).resolve().parents[1]


def _memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_repository_declares_alembic_runtime_and_config():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "alembic" in requirements.lower()
    assert (ROOT / "alembic.ini").is_file()
    assert (ROOT / "migrations" / "env.py").is_file()
    assert list((ROOT / "migrations" / "versions").glob("*.py"))


def test_empty_database_upgrades_to_head_and_is_ready():
    engine = _memory_engine()
    try:
        upgrade_schema(engine)

        tables = set(inspect(engine).get_table_names())
        assert set(Base.metadata.tables) <= tables
        assert "alembic_version" in tables
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision
        assert_schema_ready(engine)
    finally:
        engine.dispose()


def test_current_unversioned_database_is_verified_before_adoption():
    engine = _memory_engine()
    try:
        Base.metadata.create_all(engine)

        upgrade_schema(engine)

        assert "alembic_version" in inspect(engine).get_table_names()
        assert_schema_ready(engine)
    finally:
        engine.dispose()


def test_baseline_migration_can_downgrade_to_empty_database():
    engine = _memory_engine()
    try:
        upgrade_schema(engine)

        downgrade_schema(engine, "base")

        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()


def test_partial_current_schema_is_not_stamped_as_head():
    engine = _memory_engine()
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX idx_index_job_available"))

        try:
            upgrade_schema(engine)
            assert False, "部分新结构必须拒绝接管"
        except SchemaNotReadyError:
            pass

        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
