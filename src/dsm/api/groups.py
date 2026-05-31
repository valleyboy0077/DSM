"""Server groups API."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.auth import get_current_user, require_operator
from dsm.database import get_session
from dsm.models import Server, ServerGroup, server_group_members, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    color: str = Field(default="#4a90d9", max_length=16)


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    color: Optional[str] = Field(None, max_length=16)


class GroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    color: str
    server_count: int = 0
    server_ids: list[int] = []

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[GroupResponse])
async def list_groups(_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ServerGroup).order_by(ServerGroup.name))
    groups = result.scalars().all()
    response = []
    for g in groups:
        member_result = await session.execute(
            select(server_group_members.c.server_id).where(server_group_members.c.group_id == g.id)
        )
        member_ids = [m[0] for m in member_result.all()]
        response.append(GroupResponse(id=g.id, name=g.name, description=g.description, color=g.color, server_count=len(member_ids), server_ids=member_ids))
    return response


@router.post("/", response_model=GroupResponse, status_code=201)
async def create_group(data: GroupCreate, _admin: User = Depends(require_operator),session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(ServerGroup).where(ServerGroup.name == data.name))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Group '{data.name}' already exists")
    group = ServerGroup(name=data.name, description=data.description, color=data.color)
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return GroupResponse(id=group.id, name=group.name, description=group.description, color=group.color, server_count=0, server_ids=[])


@router.put("/{group_id}", response_model=GroupResponse)
async def update_group(group_id: int, data: GroupUpdate, _admin: User = Depends(require_operator),session: AsyncSession = Depends(get_session)):
    group = await session.get(ServerGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if data.name is not None:
        group.name = data.name
    if data.description is not None:
        group.description = data.description
    if data.color is not None:
        group.color = data.color
    await session.commit()
    await session.refresh(group)
    return GroupResponse(id=group.id, name=group.name, description=group.description, color=group.color, server_count=0, server_ids=[])


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: int, _admin: User = Depends(require_operator),session: AsyncSession = Depends(get_session)):
    group = await session.get(ServerGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    await session.delete(group)
    await session.commit()


@router.post("/{group_id}/servers/{server_id}", status_code=201)
async def add_server_to_group(group_id: int, server_id: int, _admin: User = Depends(require_operator),session: AsyncSession = Depends(get_session)):
    group = await session.get(ServerGroup, group_id)
    server = await session.get(Server, server_id)
    if not group or not server:
        raise HTTPException(status_code=404, detail="Group or server not found")
    stmt = server_group_members.insert().values(group_id=group_id, server_id=server_id)
    try:
        await session.execute(stmt)
        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Server already in group")


@router.delete("/{group_id}/servers/{server_id}", status_code=204)
async def remove_server_from_group(group_id: int, server_id: int, _admin: User = Depends(require_operator),session: AsyncSession = Depends(get_session)):
    stmt = server_group_members.delete().where(
        (server_group_members.c.group_id == group_id) & (server_group_members.c.server_id == server_id)
    )
    await session.execute(stmt)
    await session.commit()
