"""固定单用户在后端启动时的幂等自举测试。"""

from unittest.mock import MagicMock
import asyncio

from sqlalchemy.exc import IntegrityError


def test_ensure_app_user_creates_missing_configured_user():
    from app.startup import ensure_app_user

    db = MagicMock()
    db.get.return_value = None

    user = ensure_app_user(db)

    assert user.id == "u1"
    assert user.username == "user"
    db.add.assert_called_once_with(user)
    db.commit.assert_called_once()


def test_ensure_app_user_reuses_existing_user_without_write():
    from app.startup import ensure_app_user

    existing = MagicMock(id="u1", username="existing")
    db = MagicMock()
    db.get.return_value = existing

    assert ensure_app_user(db) is existing
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_ensure_app_user_recovers_from_concurrent_insert():
    from app.startup import ensure_app_user

    concurrent_user = MagicMock(id="u1", username="user")
    db = MagicMock()
    db.get.side_effect = [None, concurrent_user]
    db.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

    assert ensure_app_user(db) is concurrent_user
    db.rollback.assert_called_once()


def test_initialize_app_user_closes_its_session(monkeypatch):
    from app import startup

    db = MagicMock()
    monkeypatch.setattr(startup, "SessionLocal", lambda: db)
    monkeypatch.setattr(startup, "ensure_app_user", MagicMock())

    startup.initialize_app_user()

    startup.ensure_app_user.assert_called_once_with(db)
    db.close.assert_called_once()


def test_fastapi_lifespan_initializes_user_before_serving(monkeypatch):
    from app import main

    initialized = []
    monkeypatch.setattr(main, "initialize_app_user", lambda: initialized.append(True))

    async def enter_lifespan():
        async with main.lifespan(main.app):
            assert initialized == [True]

    asyncio.run(enter_lifespan())
