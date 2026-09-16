"""Safety contracts for first-run administrator provisioning."""

import logging

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
