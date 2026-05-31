"""SQLAlchemy models for Dell Server Manager.

New in v2:
- User, Role, UserRole — DSM-level auth with roles
- ServerGroup — organize servers into groups
- ServerGroupMember — many-to-many servers ↔ groups
- TempProfile, TempProfileRange — per-component temp thresholds per server
- IdracUserPropagation — tracks iDRAC user sync status
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    DateTime,
    Table,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ─── Enums ───────────────────────────────────────────────────────────────────


class ServerStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class IdracVersion(str, Enum):
    IDRAC7 = "idrac7"
    IDRAC8 = "idrac8"
    UNKNOWN = "unknown"


class FanMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    PROFILE = "profile"


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class TempComponentType(str, Enum):
    CPU = "cpu"
    DISK = "disk"
    AMBIENT = "ambient"
    VRM = "vrm"
    M2 = "m2"
    PSU = "psu"
    PERC = "perc"
    DIMM = "dimm"
    GPU = "gpu"


class PropagationStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ─── Join Tables ─────────────────────────────────────────────────────────────

server_group_members = Table(
    "server_group_members",
    Base.metadata,
    Column("server_id", Integer, ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("server_groups.id", ondelete="CASCADE"), primary_key=True),
)


# ─── Existing Models ─────────────────────────────────────────────────────────


class Server(Base):
    """Managed Dell server."""
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    ipmi_ip = Column(String(45), nullable=False, index=True)
    ipmi_user = Column(String(64), nullable=False)
    ipmi_password_enc = Column(Text, nullable=False)
    drac_version = Column(String(16), default=IdracVersion.UNKNOWN.value)
    model = Column(String(32), nullable=True)
    serial = Column(String(32), nullable=True)
    status = Column(String(16), default=ServerStatus.UNKNOWN.value)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    added_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    sensors = relationship("SensorReading", back_populates="server", cascade="all, delete-orphan")
    fan_configs = relationship("FanConfig", back_populates="server", cascade="all, delete-orphan")
    temp_profiles = relationship("TempProfile", back_populates="server", cascade="all, delete-orphan")
    groups = relationship("ServerGroup", secondary=server_group_members, back_populates="servers")
    propagations = relationship("IdracUserPropagation", back_populates="server", cascade="all, delete-orphan")


class SensorReading(Base):
    """Temperature/hardware sensor reading."""
    __tablename__ = "sensor_readings"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    sensor_type = Column(String(16), nullable=False)
    sensor_label = Column(String(64), nullable=True)
    value = Column(Float, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    server = relationship("Server", back_populates="sensors")


class FanConfig(Base):
    """Fan control configuration per server."""
    __tablename__ = "fan_configs"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)

    mode = Column(String(16), default=FanMode.AUTO.value)
    cpu_temp_min = Column(Float, default=45.0)
    cpu_temp_max = Column(Float, default=70.0)
    disk_temp_min = Column(Float, default=32.0)
    disk_temp_max = Column(Float, default=45.0)
    manual_speed = Column(Integer, default=50)
    auto_control = Column(Boolean, default=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    server = relationship("Server", back_populates="fan_configs")


# ─── New: Auth & Users ───────────────────────────────────────────────────────


class Role(Base):
    """User roles with permissions."""
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), unique=True, nullable=False)
    description = Column(String(128), nullable=True)
    permissions = Column(Text, nullable=True)  # JSON array of permission strings


class User(Base):
    """DSM-level user account."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), nullable=True)
    password_hash = Column(Text, nullable=False)  # bcrypt hashed
    password_enc = Column(Text, nullable=True)  # encrypted plaintext for iDRAC propagation
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Per-user settings
    theme = Column(String(32), default="dark")  # dark, light, blue, green, high-contrast, sepia

    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    propagations = relationship("IdracUserPropagation", back_populates="user", cascade="all, delete-orphan")


class UserRole(Base):
    """User-role assignment."""
    __tablename__ = "user_roles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)

    user = relationship("User", back_populates="roles")
    role = relationship("Role")


# ─── New: Server Groups ──────────────────────────────────────────────────────


class ServerGroup(Base):
    """Organizational group for servers (e.g., 'Production', 'Dev', 'DC-East')."""
    __tablename__ = "server_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(String(256), nullable=True)
    color = Column(String(16), default="#4a90d9")  # hex color for UI
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    servers = relationship("Server", secondary=server_group_members, back_populates="groups")


# ─── New: Temperature Profiles ───────────────────────────────────────────────


class TempProfile(Base):
    """Per-server temperature threshold profile."""
    __tablename__ = "temp_profiles"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    is_default = Column(Boolean, default=False)  # this server's active profile
    name = Column(String(64), default="Default")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    server = relationship("Server", back_populates="temp_profiles")
    ranges = relationship("TempProfileRange", back_populates="profile", cascade="all, delete-orphan")


class TempProfileRange(Base):
    """Individual component threshold within a profile."""
    __tablename__ = "temp_profile_ranges"

    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("temp_profiles.id", ondelete="CASCADE"), nullable=False)
    component_type = Column(String(32), nullable=False)  # cpu, disk, ambient, vrm, etc.
    component_label = Column(String(64), nullable=True)  # specific sensor label
    temp_min = Column(Float, nullable=True)
    temp_max = Column(Float, nullable=True)
    warning_min = Column(Float, nullable=True)
    warning_max = Column(Float, nullable=True)
    critical_min = Column(Float, nullable=True)
    critical_max = Column(Float, nullable=True)

    profile = relationship("TempProfile", back_populates="ranges")


# ─── New: iDRAC User Propagation Tracking ────────────────────────────────────


class IdracUserPropagation(Base):
    """Track user propagation to iDRAC servers."""
    __tablename__ = "idrac_user_propagations"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(16), default=PropagationStatus.PENDING.value)
    error_message = Column(Text, nullable=True)
    drac_role = Column(String(32), nullable=True)  # role assigned on iDRAC
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="propagations")
    server = relationship("Server", back_populates="propagations")
