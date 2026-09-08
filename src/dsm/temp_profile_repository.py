"""Database access helpers for temperature profiles."""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.models import TempProfile, TempProfileRange


async def get_active_temp_profile_ranges(
    session: AsyncSession, server_id: int
) -> list[TempProfileRange]:
    """Return the active/default thermal profile ranges for a server."""
    profile_result = await session.execute(
        select(TempProfile).where(
            TempProfile.server_id == server_id,
            TempProfile.is_default == True,
        )
    )
    profile = cast(Any, profile_result.scalars().first())
    if not profile:
        return []

    range_result = await session.execute(
        select(TempProfileRange).where(TempProfileRange.profile_id == profile.id)
    )
    return list(range_result.scalars().all())
