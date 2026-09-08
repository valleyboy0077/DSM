"""Database initialization and session management."""

from collections.abc import AsyncGenerator

from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dsm.config import settings
from dsm.models import Base

_db_path = str(Path(settings.db_path).expanduser())
engine = create_async_engine(
    f"sqlite+aiosqlite:///{_db_path}",
    echo=settings.debug,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    """Create all tables if they don't exist and apply lightweight column migrations."""
    import os
    from sqlalchemy import inspect

    os.makedirs(settings.data_dir, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _apply_sqlite_migrations(sync_conn):
            inspector = inspect(sync_conn)
            if "fan_configs" not in inspector.get_table_names():
                return

            fan_config_columns = {col["name"] for col in inspector.get_columns("fan_configs")}
            if "polling_seconds" not in fan_config_columns:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE fan_configs ADD COLUMN polling_seconds INTEGER NOT NULL DEFAULT 20"
                )

        await conn.run_sync(_apply_sqlite_migrations)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for DB sessions."""
    async with async_session() as session:
        yield session
