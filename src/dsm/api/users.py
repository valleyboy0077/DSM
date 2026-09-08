"""User management API — CRUD + iDRAC user propagation."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.auth import hash_password, require_admin, seed_default_roles
from dsm.crypto import encrypt_plaintext, decrypt_ciphertext
from dsm.database import get_session
from dsm.idrac_connector import IdracConnector, IdracError
from dsm.models import (
    User, Role, UserRole, Server,
    IdracUserPropagation, PropagationStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


# ─── Schemas ─────────────────────────────────────────────────────────────────


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    email: Optional[str] = Field(None, max_length=128)
    password: str = Field(..., min_length=8)
    role: str = Field(default="operator", pattern="^(admin|operator|viewer)$")
    theme: str = Field(default="dark")
    propagate_to_idrac: bool = Field(default=True)


class UserUpdate(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = Field(None, min_length=8)
    is_active: Optional[bool] = None
    role: Optional[str] = Field(None, pattern="^(admin|operator|viewer)$")
    theme: Optional[str] = None
    propagate_to_idrac: bool = Field(default=False)


class UserListResponse(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool
    is_superuser: bool
    roles: list[str] = []
    theme: str
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


class PropagationResult(BaseModel):
    server_id: int
    server_name: str
    status: str
    error_message: Optional[str] = None


class PropagationResponse(BaseModel):
    total: int
    success: int
    failed: int
    skipped: int
    results: list[PropagationResult]


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _get_user_roles(session: AsyncSession, user: User) -> list[str]:
    result = await session.execute(
        select(Role.name).join(UserRole).where(UserRole.user_id == user.id)
    )
    return [r[0] for r in result.all()]


def _user_to_response(user: User, roles: list[str]) -> dict:
    return UserListResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        roles=roles,
        theme=user.theme,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


async def _propagate_to_idrac(
    user: User,
    servers: list[Server],
    dsms_role: str,
    session: AsyncSession,
):
    """Propagate a DSM user to all iDRAC servers in inventory."""
    password = decrypt_ciphertext(user.password_enc)
    if not password:
        logger.error(f"Cannot decrypt password for user {user.username}")
        results = [
            PropagationResult(
                server_id=server.id,
                server_name=server.name,
                status=PropagationStatus.FAILED.value,
                error_message="Cannot decrypt DSM user password",
            )
            for server in servers
        ]
        return PropagationResponse(
            total=len(results),
            success=0,
            failed=len(results),
            skipped=0,
            results=results,
        )

    # Map DSM role to iDRAC role
    role_map = {
        "admin": "Administrator",
        "operator": "Operator",
        "viewer": "ReadOnly",
    }
    drac_role = role_map.get(dsms_role, "ReadOnly")

    results = []

    for server in servers:
        connector: Optional[IdracConnector] = None
        try:
            server_password = decrypt_ciphertext(server.ipmi_password_enc)
            if not server_password:
                results.append(PropagationResult(
                    server_id=server.id, server_name=server.name,
                    status=PropagationStatus.FAILED.value,
                    error_message="Cannot decrypt server credentials",
                ))
                continue

            connector = IdracConnector(
                ip=server.ipmi_ip,
                username=server.ipmi_user,
                password=server_password,
            )

            status = await connector.create_user(user.username, password, drac_role)
            error_message = None if status == PropagationStatus.SUCCESS.value else "iDRAC user propagation did not complete successfully"

            propagation = IdracUserPropagation(
                user_id=user.id,
                server_id=server.id,
                status=status,
                drac_role=drac_role,
                error_message=error_message,
            )
            session.add(propagation)

            results.append(PropagationResult(
                server_id=server.id,
                server_name=server.name,
                status=status,
                error_message=error_message,
            ))

        except IdracError as e:
            logger.error(f"Propagation failed for {user.username} on {server.name}: {e}")
            propagation = IdracUserPropagation(
                user_id=user.id,
                server_id=server.id,
                status=PropagationStatus.FAILED.value,
                error_message=str(e),
                drac_role=drac_role,
            )
            session.add(propagation)
            results.append(PropagationResult(
                server_id=server.id,
                server_name=server.name,
                status=PropagationStatus.FAILED.value,
                error_message=str(e),
            ))
        except Exception as e:
            logger.error(f"Unexpected error propagating to {server.name}: {e}")
            results.append(PropagationResult(
                server_id=server.id,
                server_name=server.name,
                status=PropagationStatus.FAILED.value,
                error_message=str(e),
            ))
        finally:
            if connector is not None:
                await connector.close()

    await session.commit()

    success = sum(1 for r in results if r.status == PropagationStatus.SUCCESS.value)
    failed = sum(1 for r in results if r.status == PropagationStatus.FAILED.value)
    skipped = sum(1 for r in results if r.status == PropagationStatus.SKIPPED.value)

    return PropagationResponse(
        total=len(results),
        success=success,
        failed=failed,
        skipped=skipped,
        results=results,
    )


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[UserListResponse])
async def list_users(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all DSM users (admin only)."""
    result = await session.execute(select(User).order_by(User.username))
    users = result.scalars().all()

    response = []
    for user in users:
        roles = await _get_user_roles(session, user)
        response.append(_user_to_response(user, roles))
    return response


@router.get("/{user_id}", response_model=UserListResponse)
async def get_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Get user details (admin only)."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    roles = await _get_user_roles(session, user)
    return _user_to_response(user, roles)


@router.post("/", response_model=dict, status_code=201)
async def create_user(
    data: UserCreate,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Create a new DSM user and optionally propagate to all iDRAC servers."""
    await seed_default_roles(session)

    # Check uniqueness
    existing = await session.execute(select(User).where(User.username == data.username))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"User '{data.username}' already exists")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        password_enc=encrypt_plaintext(data.password),
        is_active=True,
        is_superuser=(data.role == "admin"),
        theme=data.theme,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # Assign role
    role_result = await session.execute(select(Role).where(Role.name == data.role))
    role = role_result.scalars().first()
    if role:
        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.commit()

    # Propagate to iDRAC servers if requested
    propagation = None
    if data.propagate_to_idrac:
        servers_result = await session.execute(select(Server))
        servers = servers_result.scalars().all()
        if servers:
            propagation = await _propagate_to_idrac(user, servers, data.role, session)

    return {
        "status": "ok",
        "user_id": user.id,
        "username": user.username,
        "role": data.role,
        "propagation": propagation,
    }


@router.put("/{user_id}", response_model=dict)
async def update_user(
    user_id: int,
    data: UserUpdate,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Update a DSM user and optionally re-propagate to iDRAC servers."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.email is not None:
        user.email = data.email
    if data.password is not None:
        user.password_hash = hash_password(data.password)
        user.password_enc = encrypt_plaintext(data.password)
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.theme is not None:
        user.theme = data.theme

    # Update role if changed
    if data.role is not None:
        # Remove old role assignments
        await session.execute(UserRole.__table__.delete().where(UserRole.user_id == user.id))
        await session.commit()

        role_result = await session.execute(select(Role).where(Role.name == data.role))
        role = role_result.scalars().first()
        if role:
            session.add(UserRole(user_id=user.id, role_id=role.id))
            await session.commit()

    user.updated_at = datetime.now(timezone.utc)
    await session.commit()

    # Re-propagate if requested
    propagation = None
    if data.propagate_to_idrac:
        servers_result = await session.execute(select(Server))
        servers = servers_result.scalars().all()
        if servers:
            role = data.role or "operator"
            propagation = await _propagate_to_idrac(user, servers, role, session)

    return {
        "status": "ok",
        "user_id": user.id,
        "username": user.username,
        "propagation": propagation,
    }


@router.delete("/{user_id}", status_code=200)
async def delete_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Delete a DSM user and remove from all iDRAC servers."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_superuser:
        raise HTTPException(status_code=400, detail="Cannot delete superuser")

    # Remove from iDRAC servers
    servers_result = await session.execute(select(Server))
    servers = servers_result.scalars().all()

    removal_results = []
    for server in servers:
        try:
            server_password = decrypt_ciphertext(server.ipmi_password_enc)
            if not server_password:
                continue

            connector = IdracConnector(
                ip=server.ipmi_ip,
                username=server.ipmi_user,
                password=server_password,
            )
            await connector.delete_user(user.username)
            await connector.close()
            removal_results.append({"server": server.name, "status": "removed"})
        except Exception as e:
            removal_results.append({"server": server.name, "status": "failed", "error": str(e)})

    # Delete from DSM
    await session.delete(user)
    await session.commit()

    return {"status": "ok", "removed_from_idrac": removal_results}


@router.post("/{user_id}/propagate", response_model=PropagationResponse)
async def propagate_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger user propagation to all iDRAC servers."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's role
    roles = await _get_user_roles(session, user)
    role = roles[0] if roles else "viewer"

    servers_result = await session.execute(select(Server))
    servers = servers_result.scalars().all()

    if not servers:
        return PropagationResponse(total=0, success=0, failed=0, skipped=0, results=[])

    return await _propagate_to_idrac(user, servers, role, session)
