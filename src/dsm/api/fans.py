"""Fan control endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.crypto import decrypt_ciphertext
from dsm.database import get_session
from dsm.fan_control import FanController
from dsm.idrac_connector import IdracConnector, IdracConnectionError
from dsm.models import FanConfig, Server, FanMode

router = APIRouter(prefix="/fans", tags=["fans"])


class FanConfigCreate(BaseModel):
    mode: str = Field(default=FanMode.AUTO.value, pattern="^(auto|manual|profile)$")
    cpu_temp_min: float = Field(default=45.0, ge=20.0, le=80.0)
    cpu_temp_max: float = Field(default=70.0, ge=40.0, le=95.0)
    disk_temp_min: float = Field(default=32.0, ge=10.0, le=60.0)
    disk_temp_max: float = Field(default=45.0, ge=20.0, le=70.0)
    manual_speed: int = Field(default=50, ge=1, le=100)
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
    auto_control: bool
    updated_at: Optional[str]

    class Config:
        from_attributes = True


class FanControlAction(BaseModel):
    action: str = Field(pattern="^(set_manual|set_auto|reset|run_cycle)$")
    speed: Optional[int] = Field(default=None, ge=1, le=100)


@router.get("/{server_id}", response_model=FanConfigResponse)
async def get_fan_config(server_id: int, session: AsyncSession = Depends(get_session)):
    """Get fan configuration for a server."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    result = await session.execute(
        select(FanConfig).where(FanConfig.server_id == server_id)
    )
    config = result.scalars().first()

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
            manual_speed=50,
            auto_control=True,
            updated_at=None,
        )

    return config


@router.put("/{server_id}", response_model=FanConfigResponse)
async def update_fan_config(
    server_id: int,
    data: FanConfigCreate,
    session: AsyncSession = Depends(get_session),
):
    """Update fan configuration for a server."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if data.cpu_temp_max <= data.cpu_temp_min:
        raise HTTPException(status_code=400, detail="cpu_temp_max must be greater than cpu_temp_min")
    if data.disk_temp_max <= data.disk_temp_min:
        raise HTTPException(status_code=400, detail="disk_temp_max must be greater than disk_temp_min")

    result = await session.execute(
        select(FanConfig).where(FanConfig.server_id == server_id)
    )
    config = result.scalars().first()

    if config:
        config.mode = data.mode
        config.cpu_temp_min = data.cpu_temp_min
        config.cpu_temp_max = data.cpu_temp_max
        config.disk_temp_min = data.disk_temp_min
        config.disk_temp_max = data.disk_temp_max
        config.manual_speed = data.manual_speed
        config.auto_control = data.auto_control
    else:
        config = FanConfig(
            server_id=server_id,
            **data.model_dump(),
        )
        session.add(config)

    await session.commit()
    await session.refresh(config)
    return config


@router.post("/{server_id}/control", response_model=dict)
async def control_fans(
    server_id: int,
    action: FanControlAction,
    session: AsyncSession = Depends(get_session),
):
    """Execute fan control actions."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    password = decrypt_ciphertext(server.ipmi_password_enc)
    if not password:
        raise HTTPException(status_code=500, detail="Cannot decrypt credentials")

    # Get fan config
    result = await session.execute(
        select(FanConfig).where(FanConfig.server_id == server_id)
    )
    config = result.scalars().first()

    cpu_min = config.cpu_temp_min if config else 45.0
    cpu_max = config.cpu_temp_max if config else 70.0
    disk_min = config.disk_temp_min if config else 32.0
    disk_max = config.disk_temp_max if config else 45.0

    connector = IdracConnector(
        ip=server.ipmi_ip,
        username=server.ipmi_user,
        password=password,
    )

    try:
        controller = FanController(
            connector=connector,
            cpu_temp_min=cpu_min,
            cpu_temp_max=cpu_max,
            disk_temp_min=disk_min,
            disk_temp_max=disk_max,
        )

        if action.action == "set_manual":
            if action.speed is None:
                raise HTTPException(status_code=400, detail="speed is required for set_manual")
            success = await controller.set_manual_speed(action.speed)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to set manual fan speed")
            return {"status": "ok", "message": f"Fan speed set to {action.speed}%"}

        elif action.action == "set_auto":
            success = await controller.set_auto_mode()
            if not success:
                raise HTTPException(status_code=500, detail="Failed to enable auto mode")
            return {"status": "ok", "message": "Auto fan control enabled"}

        elif action.action == "reset":
            success = await controller.reset_to_default()
            if not success:
                raise HTTPException(status_code=500, detail="Failed to reset to default")
            return {"status": "ok", "message": "Fan control reset to iDRAC default"}

        elif action.action == "run_cycle":
            result = await controller.control_cycle()
            return {
                "status": "ok",
                "fan_percent": result.target_fan_percent,
                "cpu_temp": result.cpu_temp,
                "disk_temp": result.disk_temp,
                "ambient_temp": result.ambient_temp,
                "action": result.action_taken,
                "reason": result.reason,
            }

    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to iDRAC: {e}")
    finally:
        await connector.close()
