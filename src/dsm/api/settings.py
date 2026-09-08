"""iDRAC settings API — scrape all traditional iDRAC WebUI menus."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.api.idrac import execute_idrac_request
from dsm.auth import get_current_user
from dsm.database import get_session
from dsm.models import Server, User

router = APIRouter(prefix="/settings", tags=["settings"])


async def _request_settings(
    server_id: int,
    session: AsyncSession,
    method_name: str,
    **kwargs,
):
    """Run a settings read with the established iDRAC API error contract."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return await execute_idrac_request(
        server,
        lambda connector: getattr(connector, method_name)(**kwargs),
        credential_error_detail="Cannot decrypt server credentials",
    )


# ─── Network ─────────────────────────────────────────────────────────────────

@router.get("/network/{server_id}")
async def get_network(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get iDRAC network settings."""
    return await _request_settings(server_id, session, "get_network_settings")


# ─── System Event Log ────────────────────────────────────────────────────────

@router.get("/sel/{server_id}")
async def get_sel(
    server_id: int,
    clear: bool = False,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get System Event Log."""
    return await _request_settings(server_id, session, "get_sel", clear=clear)


# ─── Hardware Inventory ──────────────────────────────────────────────────────

@router.get("/hardware/{server_id}")
async def get_hardware(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get full hardware inventory."""
    return await _request_settings(server_id, session, "get_hardware_inventory")


# ─── Power Management ────────────────────────────────────────────────────────

@router.get("/power/{server_id}")
async def get_power_settings(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get power management settings."""
    return await _request_settings(server_id, session, "get_power_settings")


# ─── Storage ─────────────────────────────────────────────────────────────────

@router.get("/storage/{server_id}")
async def get_storage(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get RAID/storage configuration."""
    return await _request_settings(server_id, session, "get_storage_settings")


# ─── BIOS ────────────────────────────────────────────────────────────────────

@router.get("/bios/{server_id}")
async def get_bios(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get BIOS settings."""
    return await _request_settings(server_id, session, "get_bios_settings")


# ─── Security ────────────────────────────────────────────────────────────────

@router.get("/security/{server_id}")
async def get_security(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get security settings."""
    return await _request_settings(server_id, session, "get_security_settings")


# ─── Firmware ────────────────────────────────────────────────────────────────

@router.get("/firmware/{server_id}")
async def get_firmware(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get firmware info."""
    return await _request_settings(server_id, session, "get_firmware_info")


# ─── Alerts ──────────────────────────────────────────────────────────────────

@router.get("/alerts/{server_id}")
async def get_alerts(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get alert/notification settings."""
    return await _request_settings(server_id, session, "get_alerts_settings")


# ─── Virtual Media ───────────────────────────────────────────────────────────

@router.get("/virtual-media/{server_id}")
async def get_virtual_media(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get virtual media status."""
    return await _request_settings(server_id, session, "get_virtual_media")


# ─── iDRAC Users ─────────────────────────────────────────────────────────────

@router.get("/idrac-users/{server_id}")
async def get_idrac_users(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List iDRAC user accounts on a server."""
    return await _request_settings(server_id, session, "list_idrac_users")
