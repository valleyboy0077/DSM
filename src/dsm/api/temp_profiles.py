"""Temperature profile API — per-component thresholds per server."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.auth import get_current_user, require_operator
from dsm.database import get_session
from dsm.models import TempProfile, TempProfileRange, FanConfig, Server, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/temp-profiles", tags=["temp-profiles"])


class TempRangeCreate(BaseModel):
    component_type: str
    component_label: Optional[str] = None
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    warning_min: Optional[float] = None
    warning_max: Optional[float] = None
    critical_min: Optional[float] = None
    critical_max: Optional[float] = None


class TempProfileCreate(BaseModel):
    server_id: int
    name: str = Field(default="Default", max_length=64)
    is_default: bool = False
    ranges: list[TempRangeCreate] = []


class TempRangeResponse(BaseModel):
    id: int
    component_type: str
    component_label: Optional[str] = None
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    warning_min: Optional[float] = None
    warning_max: Optional[float] = None
    critical_min: Optional[float] = None
    critical_max: Optional[float] = None


class TempProfileResponse(BaseModel):
    id: int
    server_id: int
    server_name: Optional[str] = None
    is_default: bool
    name: str
    ranges: list[TempRangeResponse] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@router.get("/server/{server_id}", response_model=list[TempProfileResponse])
async def list_server_profiles(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List all temp profiles for a server."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    result = await session.execute(
        select(TempProfile).where(TempProfile.server_id == server_id).order_by(TempProfile.is_default.desc())
    )
    profiles = result.scalars().all()

    response = []
    for p in profiles:
        ranges_result = await session.execute(
            select(TempProfileRange).where(TempProfileRange.profile_id == p.id)
        )
        ranges = ranges_result.scalars().all()
        response.append(TempProfileResponse(
            id=p.id, server_id=p.server_id, server_name=server.name,
            is_default=p.is_default, name=p.name,
            ranges=[TempRangeResponse(
                id=r.id, component_type=r.component_type, component_label=r.component_label,
                temp_min=r.temp_min, temp_max=r.temp_max,
                warning_min=r.warning_min, warning_max=r.warning_max,
                critical_min=r.critical_min, critical_max=r.critical_max,
            ) for r in ranges],
            created_at=p.created_at.isoformat() if p.created_at else None,
            updated_at=p.updated_at.isoformat() if p.updated_at else None,
        ))
    return response


@router.post("/server/{server_id}", response_model=TempProfileResponse, status_code=201)
async def create_profile(
    server_id: int,
    data: TempProfileCreate,
    _admin: User = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Create a temp profile for a server."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # If this is the default, clear old default
    if data.is_default:
        old = await session.execute(
            select(TempProfile).where(TempProfile.server_id == server_id, TempProfile.is_default == True)
        )
        for o in old.scalars().all():
            o.is_default = False

    profile = TempProfile(server_id=server_id, name=data.name, is_default=data.is_default)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    # Add ranges
    for r in data.ranges:
        session.add(TempProfileRange(
            profile_id=profile.id,
            component_type=r.component_type,
            component_label=r.component_label,
            temp_min=r.temp_min, temp_max=r.temp_max,
            warning_min=r.warning_min, warning_max=r.warning_max,
            critical_min=r.critical_min, critical_max=r.critical_max,
        ))
    await session.commit()

    return TempProfileResponse(
        id=profile.id, server_id=profile.server_id, server_name=server.name,
        is_default=profile.is_default, name=profile.name,
        ranges=[],
        created_at=profile.created_at.isoformat() if profile.created_at else None,
    )


@router.put("/{profile_id}", response_model=TempProfileResponse)
async def update_profile(
    profile_id: int,
    data: TempProfileCreate,
    _admin: User = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Update a temp profile."""
    profile = await session.get(TempProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if data.name:
        profile.name = data.name
    profile.updated_at = datetime.now(timezone.utc)

    # Handle default flag
    if data.is_default and not profile.is_default:
        old = await session.execute(
            select(TempProfile).where(TempProfile.server_id == profile.server_id, TempProfile.is_default == True)
        )
        for o in old.scalars().all():
            o.is_default = False
        profile.is_default = True

    # Update ranges — delete old, insert new
    await session.execute(TempProfileRange.__table__.delete().where(TempProfileRange.profile_id == profile_id))
    await session.commit()

    for r in data.ranges:
        session.add(TempProfileRange(
            profile_id=profile.id,
            component_type=r.component_type,
            component_label=r.component_label,
            temp_min=r.temp_min, temp_max=r.temp_max,
            warning_min=r.warning_min, warning_max=r.warning_max,
            critical_min=r.critical_min, critical_max=r.critical_max,
        ))
    await session.commit()

    server = await session.get(Server, profile.server_id)

    return TempProfileResponse(
        id=profile.id, server_id=profile.server_id, server_name=server.name if server else None,
        is_default=profile.is_default, name=profile.name,
        ranges=[],
        updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
    )


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: int,
    _admin: User = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Delete a temp profile."""
    profile = await session.get(TempProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    await session.delete(profile)
    await session.commit()


@router.get("/effective/{server_id}")
async def get_effective_thresholds(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get the effective (active) temperature thresholds for a server.
    
    Combines FanConfig with the active TempProfile ranges.
    Used by the fan controller."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get fan config
    fan_config = None
    fc_result = await session.execute(select(FanConfig).where(FanConfig.server_id == server_id))
    fan_config = fc_result.scalars().first()

    # Get active temp profile
    profile_result = await session.execute(
        select(TempProfile).where(TempProfile.server_id == server_id, TempProfile.is_default == True)
    )
    profile = profile_result.scalars().first()

    ranges = {}
    if profile:
        range_result = await session.execute(
            select(TempProfileRange).where(TempProfileRange.profile_id == profile.id)
        )
        for r in range_result.scalars().all():
            ranges[r.component_type] = {
                "temp_min": r.temp_min,
                "temp_max": r.temp_max,
                "warning_min": r.warning_min,
                "warning_max": r.warning_max,
                "critical_min": r.critical_min,
                "critical_max": r.critical_max,
            }

    return {
        "server_id": server_id,
        "server_name": server.name,
        "fan_config": {
            "mode": fan_config.mode if fan_config else "auto",
            "cpu_temp_min": fan_config.cpu_temp_min if fan_config else 45.0,
            "cpu_temp_max": fan_config.cpu_temp_max if fan_config else 70.0,
            "disk_temp_min": fan_config.disk_temp_min if fan_config else 32.0,
            "disk_temp_max": fan_config.disk_temp_max if fan_config else 45.0,
        },
        "profile": profile.name if profile else None,
        "ranges": ranges,
    }
