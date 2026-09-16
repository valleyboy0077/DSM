"""Safety contracts for first-run administrator provisioning."""

import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dsm.auth import seed_default_admin, seed_default_roles, verify_password
from dsm.config import settings
from dsm.models import Base, User


@pytest.mark.asyncio
async def test_required_bootstrap_password_blocks_empty_database_without_logging_secret(tmp_path, monkeypatch, caplog):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dsm.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(settings, "require_bootstrap_admin", True)
    caplog.set_level(logging.INFO)
    try:
        async with session_factory() as session:
            with pytest.raises(RuntimeError, match="DSM_BOOTSTRAP_ADMIN_PASSWORD must be set"):
                await seed_default_admin(session)
            assert (await session.execute(select(User))).scalars().first() is None
        assert "admin" not in caplog.text.lower()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["", " \t\n "])
async def test_required_bootstrap_password_rejects_blank_values(tmp_path, monkeypatch, password):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dsm.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(settings, "require_bootstrap_admin", True)
    try:
        async with session_factory() as session:
            with pytest.raises(RuntimeError, match="DSM_BOOTSTRAP_ADMIN_PASSWORD must be set"):
                await seed_default_admin(session, password=password)
            assert (await session.execute(select(User))).scalars().first() is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_non_required_bootstrap_preserves_default_admin_password(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dsm.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(settings, "require_bootstrap_admin", False)
    try:
        async with session_factory() as session:
            await seed_default_roles(session)
            await seed_default_admin(session)
            user = (await session.execute(select(User).where(User.username == "admin"))).scalars().one()
        assert verify_password("admin", user.password_hash)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_required_bootstrap_password_creates_configured_admin(tmp_path, monkeypatch, caplog):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dsm.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    secret = "a-unique-bootstrap-password"
    monkeypatch.setattr(settings, "require_bootstrap_admin", True)
    caplog.set_level(logging.INFO)
    try:
        async with session_factory() as session:
            await seed_default_roles(session)
            await seed_default_admin(session, username="bootstrap", password=secret)
            user = (await session.execute(select(User).where(User.username == "bootstrap"))).scalars().one()
        assert verify_password(secret, user.password_hash)
        assert secret not in caplog.text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_login_uses_bootstrap_settings_without_name_error(monkeypatch):
    from dsm.api import auth as auth_api

    captured = {}
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", is_active=True, is_superuser=True, theme="dark")

    async def seed_admin(_session, **kwargs):
        captured.update(kwargs)

    class Result:
        def scalars(self):
            return self

        def first(self):
            return user

    class Session:
        async def execute(self, _query):
            return Result()

    async def roles(_session, _user):
        return ["admin"]

    async def seed_roles(_session):
        return None

    monkeypatch.setattr(auth_api, "seed_default_roles", seed_roles)
    monkeypatch.setattr(auth_api, "seed_default_admin", seed_admin)
    monkeypatch.setattr(auth_api, "verify_password", lambda password, password_hash: True)
    monkeypatch.setattr(auth_api, "_get_user_roles", roles)
    monkeypatch.setattr(auth_api, "create_token", lambda *args: "token")

    response = await auth_api.login(auth_api.LoginRequest(username="admin", password="password"), Session())

    assert response.access_token == "token"
    assert captured == {
        "username": settings.bootstrap_admin_username,
        "password": settings.bootstrap_admin_password,
        "email": settings.bootstrap_admin_email,
    }
