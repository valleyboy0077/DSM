"""Authentication API routes — login, register, me, password change."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.auth import (
    hash_password,
    verify_password,
    create_token,
    get_current_user,
    decode_token,
    seed_default_roles,
    seed_default_admin,
)
from dsm.config import settings
from dsm.crypto import encrypt_plaintext
from dsm.database import get_session
from dsm.models import User, Role, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Request/Response Schemas ────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    email: Optional[str] = Field(None, max_length=128)
    password: str = Field(..., min_length=8)
    theme: str = Field(default="dark")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    is_superuser: bool
    roles: list[str]
    theme: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool
    is_superuser: bool
    roles: list[str] = []
    theme: str
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_user(cls, user: User, roles: list[str]) -> "UserResponse":
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            roles=roles,
            theme=user.theme,
            created_at=user.created_at.isoformat() if user.created_at else None,
        )


async def _get_user_roles(session: AsyncSession, user: User) -> list[str]:
    result = await session.execute(
        select(Role.name).join(UserRole).where(UserRole.user_id == user.id)
    )
    return [r[0] for r in result.all()]


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, session: AsyncSession = Depends(get_session)):
    """Authenticate user and return JWT token."""
    # Seed defaults on first login
    await seed_default_roles(session)
    await seed_default_admin(session)

    result = await session.execute(select(User).where(User.username == data.username))
    user = result.scalars().first()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    roles = await _get_user_roles(session, user)
    token = create_token(user.id, user.username, user.is_superuser, roles)

    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        is_superuser=user.is_superuser,
        roles=roles,
        theme=user.theme,
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(data: RegisterRequest, session: AsyncSession = Depends(get_session)):
    """Register a new user. Only allowed if no users exist yet (self-service first admin)."""
    existing_count = await session.execute(select(User))
    if existing_count.scalars().all():
        raise HTTPException(
            status_code=403,
            detail="Self-registration disabled. Contact admin to create account.",
        )

    # Seed defaults first
    await seed_default_roles(session)

    # Check username uniqueness
    existing = await session.execute(select(User).where(User.username == data.username))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        password_enc=encrypt_plaintext(data.password),
        is_active=True,
        is_superuser=True,  # first user is superadmin
        theme=data.theme,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    logger.info(f"First admin user registered: {data.username}")

    # Assign admin role
    role_result = await session.execute(select(Role).where(Role.name == "admin"))
    role = role_result.scalars().first()
    if role:
        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.commit()

    token = create_token(user.id, user.username, True, ["admin"])
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        is_superuser=True,
        roles=["admin"],
        theme=data.theme,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get current user info."""
    roles = await _get_user_roles(session, user)
    return UserResponse.from_user(user, roles)


@router.put("/password")
async def change_password(
    data: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Change current user's password."""
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    user.password_hash = hash_password(data.new_password)
    user.password_enc = encrypt_plaintext(data.new_password)  # update encrypted copy for propagation
    user.updated_at = datetime.now(timezone.utc)
    await session.commit()

    return {"status": "ok", "message": "Password changed successfully"}


@router.put("/theme")
async def update_theme(
    theme: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Update user's theme preference."""
    valid_themes = ["dark", "light", "blue", "green", "high-contrast", "sepia"]
    if theme not in valid_themes:
        raise HTTPException(status_code=400, detail=f"Invalid theme. Choose from: {', '.join(valid_themes)}")

    user.theme = theme
    user.updated_at = datetime.now(timezone.utc)
    await session.commit()

    return {"status": "ok", "theme": theme}


@router.get("/refresh", response_model=TokenResponse)
async def refresh_token(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Refresh the JWT token."""
    roles = await _get_user_roles(session, user)
    token = create_token(user.id, user.username, user.is_superuser, roles)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        is_superuser=user.is_superuser,
        roles=roles,
        theme=user.theme,
    )
