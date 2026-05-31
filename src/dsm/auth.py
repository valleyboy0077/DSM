"""Authentication module — JWT tokens + bcrypt password hashing."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.config import settings
from dsm.database import get_session
from dsm.models import User, Role, UserRole

logger = logging.getLogger(__name__)

# JWT configuration
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 24  # 24 hours
JWT_ISSUER = "dsm"

security = HTTPBearer(auto_error=False)


def get_jwt_secret() -> str:
    """Derive JWT secret from encryption key. In production, use a separate secret."""
    key = settings.encryption_key
    return f"dsm-jwt-{key}"


def hash_password(plaintext: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))


def create_token(user_id: int, username: str, is_superuser: bool, roles: list[str]) -> str:
    """Create a JWT token for the user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "is_superuser": is_superuser,
        "roles": roles,
        "iss": JWT_ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        return jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency: get the current authenticated user from the JWT token."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    user_id = int(payload["sub"])

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    return user


async def require_role(
    required_role: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency: check if user has a specific role."""
    if user.is_superuser:
        return user

    result = await session.execute(
        select(UserRole).where(UserRole.user_id == user.id).join(Role).where(Role.name == required_role)
    )
    if not result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{required_role}' required",
        )
    return user


async def require_admin(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Shorthand for require_role('admin')."""
    return await require_role("admin", user, session)


async def require_operator(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Shorthand for require_role('operator') — allows admin too."""
    # Check superuser
    if user.is_superuser:
        return user

    result = await session.execute(
        select(UserRole)
        .where(UserRole.user_id == user.id)
        .join(Role)
        .where(Role.name.in_(["admin", "operator"]))
    )
    if not result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or Operator role required",
        )
    return user


async def seed_default_roles(session: AsyncSession):
    """Create default roles if they don't exist."""
    roles = [
        ("admin", "Full access to all features", '["read","write","delete","manage_users","propagate_users"]'),
        ("operator", "Can manage servers and fans, view sensors", '["read","write","manage_fans","power_control"]'),
        ("viewer", "Read-only access to sensors and server info", '["read"]'),
    ]

    for name, desc, perms in roles:
        existing = await session.execute(select(Role).where(Role.name == name))
        if not existing.scalars().first():
            session.add(Role(name=name, description=desc, permissions=perms))
    await session.commit()


async def seed_default_admin(
    session: AsyncSession,
    username: str = "admin",
    password: str = "admin",
    email: str = "admin@localhost",
):
    """Create a default admin user if none exists."""
    existing = await session.execute(select(User))
    if existing.scalars().first():
        return

    from dsm.crypto import encrypt_plaintext

    admin = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        password_enc=encrypt_plaintext(password),
        is_active=True,
        is_superuser=True,
        theme="dark",
    )
    session.add(admin)
    await session.commit()
    logger.info(f"Default admin user created: {username}")
