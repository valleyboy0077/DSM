"""Database initialization and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dsm.config import settings
from dsm.models import Base

engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=settings.debug,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    """Create all tables if they don't exist."""
    import os
    os.makedirs(settings.data_dir, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for DB sessions."""
    async with async_session() as session:
        yield session
