"""Fan control endpoints."""

from datetime import datetime, timezone
from typing import Any, Optional, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.auth import get_current_user, require_operator
from dsm.database import get_session
from dsm.fan_control import FanController
from dsm.api.idrac import execute_idrac_request
from dsm.idrac_connector import IdracConnector
from dsm.models import FanConfig, FanMode, Server
from dsm.temp_profile_repository import get_active_temp_profile_ranges

router = APIRouter(prefix="/fans", tags=["fans"])

DEFAULT_POLLING_SECONDS = 20


async def _normalize_fan_config(session: AsyncSession, config: FanConfig) -> FanConfig:
    """Backfill legacy fan-config rows that predate required polling defaults."""
    if config.polling_seconds is None:
        config.polling_seconds = DEFAULT_POLLING_SECONDS
        await session.commit()
        await session.refresh(config)
    return config


async def _get_or_create_fan_config(session: AsyncSession, server_id: int) -> FanConfig:
    """Return an existing fan config or create one with API defaults.

    Manual/auto actions need a persisted row so the UI reflects the live state
    after direct fan-control actions, not only after a full config PUT.
    """
    result = await session.execute(
        select(FanConfig).where(FanConfig.server_id == server_id)
    )
    config = cast(Any, result.scalars().first())
    if config:
        return await _normalize_fan_config(session, config)

    config = cast(Any, FanConfig(
        server_id=server_id,
        mode=FanMode.AUTO.value,
        cpu_temp_min=45.0,
        cpu_temp_max=70.0,
        disk_temp_min=32.0,
        disk_temp_max=45.0,
        manual_speed=25,
        polling_seconds=DEFAULT_POLLING_SECONDS,
        auto_control=True,
    ))
    session.add(config)
    await session.flush()
    return config


async def _persist_fan_mode(
    session: AsyncSession,
    config: Any,
    *,
    mode: str,
    auto_control: bool,
    manual_speed: Optional[int] = None,
) -> None:
    """Persist the effective fan-control mode after a live control action."""
    config.mode = mode
    config.auto_control = auto_control
    if manual_speed is not None:
        config.manual_speed = manual_speed
    await session.commit()
    await session.refresh(config)


class FanConfigCreate(BaseModel):
    mode: str = Field(default=FanMode.AUTO.value, pattern="^(auto|manual|profile)$")
    cpu_temp_min: float = Field(default=45.0, ge=20.0, le=80.0)
    cpu_temp_max: float = Field(default=70.0, ge=40.0, le=95.0)
    disk_temp_min: float = Field(default=32.0, ge=10.0, le=60.0)
    disk_temp_max: float = Field(default=45.0, ge=20.0, le=70.0)
    manual_speed: int = Field(default=25, ge=1, le=100)
    polling_seconds: int = Field(default=20, ge=5, le=3600)
    auto_control: bool = Field(default=True)


class FanConfigResponse(BaseModel):
    id: int
    server_id: int
    mode: str
    cpu_temp_min: float
    cpu_temp_max: float
    disk_temp_min: float
    disk_temp_max: float
    manual_speed: int
    polling_seconds: int
    auto_control: bool
    current_target_percent: Optional[int] = None
    updated_at: Optional[datetime] = None

    @field_serializer("updated_at")
    def serialize_updated_at(self, value: datetime | None, _info) -> str | None:
        return value.isoformat() if value else None

    model_config = {"from_attributes": True}


class FanControlAction(BaseModel):
    action: str = Field(pattern="^(set_manual|set_auto|reset|run_cycle)$")
    speed: Optional[int] = Field(default=None, ge=1, le=100)


class FanTelemetryItem(BaseModel):
    name: str
    member_id: str
    rpm: int
    percent: Optional[int] = None
    percent_source: str
    health: str
    source: str


class FanTelemetryResponse(BaseModel):
    server_id: int
    server_name: str
    collected_at: datetime
    source: str
    fans: list[FanTelemetryItem]

    @field_serializer("collected_at")
    def serialize_collected_at(self, value: datetime, _info) -> str:
        return value.isoformat()


@router.get("/{server_id}", response_model=FanConfigResponse)
async def get_fan_config(
    server_id: int,
    _user: Any = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get fan configuration for a server."""
    server = cast(Any, await session.get(Server, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    from dsm.api.sensors import poller as sensor_poller
    current_target_percent = sensor_poller._last_fan_control_target.get(server_id)

    result = await session.execute(
        select(FanConfig).where(FanConfig.server_id == server_id)
    )
    config = cast(Any, result.scalars().first())

    if not config:
        # Return defaults
        return FanConfigResponse(
            id=0,
            server_id=server_id,
            mode=FanMode.AUTO.value,
            cpu_temp_min=45.0,
            cpu_temp_max=70.0,
            disk_temp_min=32.0,
            disk_temp_max=45.0,
            manual_speed=25,
            polling_seconds=DEFAULT_POLLING_SECONDS,
            auto_control=True,
            current_target_percent=current_target_percent,
            updated_at=None,
        )

    normalized = await _normalize_fan_config(session, config)
    payload = FanConfigResponse.model_validate(normalized).model_dump(exclude={"current_target_percent"})
    return FanConfigResponse(
        **payload,
        current_target_percent=current_target_percent,
    )


@router.get("/{server_id}/telemetry", response_model=FanTelemetryResponse)
async def get_fan_telemetry(
    server_id: int,
    _user: Any = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get live fan RPM + percentage readings for a server."""
    server = cast(Any, await session.get(Server, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    fans = await execute_idrac_request(
        server,
        lambda connector: connector.get_fan_inventory(),
        credential_error_detail="Cannot decrypt credentials",
        connection_error_detail=lambda error: f"Cannot connect to iDRAC: {error}",
        idrac_error_detail=lambda error: f"iDRAC telemetry query failed: {error}",
    )
    source = fans[0]["source"] if fans else "unknown"
    return FanTelemetryResponse(
        server_id=server.id,
        server_name=server.name,
        collected_at=datetime.now(timezone.utc),
        source=source,
        fans=[FanTelemetryItem(**fan) for fan in fans],
    )


@router.put("/{server_id}", response_model=FanConfigResponse)
async def update_fan_config(
    server_id: int,
    data: FanConfigCreate,
    _user: Any = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Update fan configuration for a server."""
    server = cast(Any, await session.get(Server, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if data.cpu_temp_max <= data.cpu_temp_min:
        raise HTTPException(status_code=400, detail="cpu_temp_max must be greater than cpu_temp_min")
    if data.disk_temp_max <= data.disk_temp_min:
        raise HTTPException(status_code=400, detail="disk_temp_max must be greater than disk_temp_min")

    result = await session.execute(
        select(FanConfig).where(FanConfig.server_id == server_id)
    )
    config = cast(Any, result.scalars().first())

    if config:
        config.mode = data.mode
        config.cpu_temp_min = data.cpu_temp_min
        config.cpu_temp_max = data.cpu_temp_max
        config.disk_temp_min = data.disk_temp_min
        config.disk_temp_max = data.disk_temp_max
        config.manual_speed = data.manual_speed
        config.polling_seconds = data.polling_seconds
        config.auto_control = data.auto_control
    else:
        config = cast(Any, FanConfig(
            server_id=server_id,
            **data.model_dump(),
        ))
        session.add(config)

    await session.commit()
    await session.refresh(config)
    return config


@router.post("/{server_id}/control", response_model=dict)
async def control_fans(
    server_id: int,
    action: FanControlAction,
    _user: Any = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Execute fan control actions."""
    server = cast(Any, await session.get(Server, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    async def execute_control(connector: IdracConnector) -> dict:
        # Get the persisted config row so button-triggered state changes are saved.
        config = await _get_or_create_fan_config(session, server_id)
        cpu_min = config.cpu_temp_min if config else 45.0
        cpu_max = config.cpu_temp_max if config else 70.0
        disk_min = config.disk_temp_min if config else 32.0
        disk_max = config.disk_temp_max if config else 45.0
        controller = FanController(
            connector=connector,
            cpu_temp_min=cpu_min,
            cpu_temp_max=cpu_max,
            disk_temp_min=disk_min,
            disk_temp_max=disk_max,
        )
        profile_ranges = await get_active_temp_profile_ranges(session, server_id)

        if action.action == "set_manual":
            if action.speed is None:
                raise HTTPException(status_code=400, detail="speed is required for set_manual")
            success = await controller.set_manual_speed(action.speed)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to set manual fan speed")
            from dsm.api.sensors import poller as sensor_poller
            sensor_poller._last_fan_control_target[server_id] = controller._current_fan_percent
            sensor_poller._last_fan_control_at.pop(server_id, None)
            await _persist_fan_mode(
                session,
                config,
                mode=FanMode.MANUAL.value,
                auto_control=False,
                manual_speed=controller._current_fan_percent,
            )
            if getattr(connector, "_fan_control_backend", None) == "racadm":
                message = (
                    f"RACADM minimum fan-speed floor set to {controller._current_fan_percent}%; "
                    "iDRAC may run the fans higher"
                )
            else:
                message = (
                    f"Manual fan duty command sent at {controller._current_fan_percent}%; "
                    "confirm effective PWM and RPM in live telemetry"
                )
            return {"status": "ok", "message": message}

        elif action.action == "set_auto":
            success = await controller.set_auto_mode()
            if not success:
                raise HTTPException(status_code=500, detail="Failed to enable auto mode")
            from dsm.api.sensors import poller as sensor_poller
            sensor_poller._last_fan_control_target.pop(server_id, None)
            sensor_poller._last_fan_control_at.pop(server_id, None)
            await _persist_fan_mode(
                session,
                config,
                mode=FanMode.AUTO.value,
                auto_control=True,
            )
            return {"status": "ok", "message": "Auto fan control enabled"}

        elif action.action == "reset":
            success = await controller.reset_to_default()
            if not success:
                raise HTTPException(status_code=500, detail="Failed to reset to default")
            from dsm.api.sensors import poller as sensor_poller
            sensor_poller._last_fan_control_target.pop(server_id, None)
            sensor_poller._last_fan_control_at.pop(server_id, None)
            await _persist_fan_mode(
                session,
                config,
                mode=FanMode.PROFILE.value,
                auto_control=True,
            )
            return {"status": "ok", "message": "Fan control reset to iDRAC default"}

        elif action.action == "run_cycle":
            from dsm.api.sensors import poller as sensor_poller
            current_fan_percent = sensor_poller._last_fan_control_target.get(server_id)
            if current_fan_percent is None:
                # Do not treat the saved manual setting as live PWM.  It may
                # describe an old override, and using it could reduce a fan
                # whose telemetry does not expose a duty percentage.
                current_fan_percent = None
            result = await controller.control_cycle(
                current_fan_percent=current_fan_percent,
                profile_ranges=profile_ranges,
            )
            if result.action_taken in {"increased", "decreased"}:
                sensor_poller._last_fan_control_target[server_id] = result.target_fan_percent
                sensor_poller._last_fan_control_at[server_id] = datetime.now(timezone.utc)
            return {
                "status": "ok",
                "fan_percent": result.target_fan_percent,
                "cpu_temp": result.cpu_temp,
                "disk_temp": result.disk_temp,
                "ambient_temp": result.ambient_temp,
                "action": result.action_taken,
                "reason": result.reason,
            }

    return await execute_idrac_request(
        server,
        execute_control,
        credential_error_detail="Cannot decrypt credentials",
        connection_error_detail=lambda error: f"Cannot connect to iDRAC: {error}",
    )
