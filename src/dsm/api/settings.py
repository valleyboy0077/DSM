"""iDRAC settings API — scrape all traditional iDRAC WebUI menus."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.auth import get_current_user
from dsm.crypto import decrypt_ciphertext
from dsm.database import get_session
from dsm.idrac_connector import IdracConnector, IdracError, IdracConnectionError
from dsm.models import Server, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


async def _get_connector(server_id: int, session: AsyncSession) -> IdracConnector:
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    password = decrypt_ciphertext(server.ipmi_password_enc)
    if not password:
        raise HTTPException(status_code=500, detail="Cannot decrypt server credentials")
    return IdracConnector(ip=server.ipmi_ip, username=server.ipmi_user, password=password)


# ─── Network ─────────────────────────────────────────────────────────────────

@router.get("/network/{server_id}")
async def get_network(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get iDRAC network settings."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_network_settings()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── System Event Log ────────────────────────────────────────────────────────

@router.get("/sel/{server_id}")
async def get_sel(
    server_id: int,
    clear: bool = False,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get System Event Log."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_sel(clear=clear)
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── Hardware Inventory ──────────────────────────────────────────────────────

@router.get("/hardware/{server_id}")
async def get_hardware(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get full hardware inventory."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_hardware_inventory()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── Power Management ────────────────────────────────────────────────────────

@router.get("/power/{server_id}")
async def get_power_settings(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get power management settings."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_power_settings()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── Storage ─────────────────────────────────────────────────────────────────

@router.get("/storage/{server_id}")
async def get_storage(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get RAID/storage configuration."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_storage_settings()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── BIOS ────────────────────────────────────────────────────────────────────

@router.get("/bios/{server_id}")
async def get_bios(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get BIOS settings."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_bios_settings()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── Security ────────────────────────────────────────────────────────────────

@router.get("/security/{server_id}")
async def get_security(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get security settings."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_security_settings()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── Firmware ────────────────────────────────────────────────────────────────

@router.get("/firmware/{server_id}")
async def get_firmware(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get firmware info."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_firmware_info()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── Alerts ──────────────────────────────────────────────────────────────────

@router.get("/alerts/{server_id}")
async def get_alerts(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get alert/notification settings."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_alerts_settings()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── Virtual Media ───────────────────────────────────────────────────────────

@router.get("/virtual-media/{server_id}")
async def get_virtual_media(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get virtual media status."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.get_virtual_media()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()


# ─── iDRAC Users ─────────────────────────────────────────────────────────────

@router.get("/idrac-users/{server_id}")
async def get_idrac_users(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List iDRAC user accounts on a server."""
    conn = await _get_connector(server_id, session)
    try:
        return await conn.list_idrac_users()
    except IdracConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await conn.close()
