"""Database initialization and session management."""

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dsm.config import settings
from dsm.migrations import run_migrations

_db_path = str(Path(settings.db_path).expanduser())
engine = create_async_engine(
    f"sqlite+aiosqlite:///{_db_path}",
    echo=settings.debug,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    """Create or safely upgrade the configured database to the latest schema."""
    import os

    os.makedirs(settings.data_dir, exist_ok=True)
    await run_migrations(engine)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for DB sessions."""
    async with async_session() as session:
        yield session
