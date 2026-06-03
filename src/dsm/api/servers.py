"""Server management REST endpoints.

Handles CRUD operations for managed Dell servers:
- POST /servers/     — Add a new server
- GET  /servers/     — List all servers
- GET  /servers/{id} — Get server details
- PUT  /servers/{id} — Update server
- DELETE /servers/{id} — Remove server
- POST /servers/{id}/power — Power control (on/off/restart/shutdown)
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.auth import get_current_user, require_operator
from dsm.crypto import decrypt_ciphertext, encrypt_plaintext
from dsm.database import get_session
from dsm.models import Server, User

router = APIRouter(prefix="/servers", tags=["servers"])


class ServerCreate(BaseModel):
    """Request body for adding a new server."""
    name: str = Field(..., min_length=1, max_length=64, description="Human-friendly server name")
    ipmi_ip: str = Field(..., min_length=1, description="iDRAC/IPMI management IP address")
    ipmi_user: str = Field(..., min_length=1, description="iDRAC username")
    ipmi_password: str = Field(..., min_length=1, description="iDRAC password (encrypted at rest)")


class ServerUpdate(BaseModel):
    """Request body for updating a server. All fields optional."""
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    ipmi_ip: Optional[str] = None
    ipmi_user: Optional[str] = None
    ipmi_password: Optional[str] = None


class ServerResponse(BaseModel):
    """Serialized server response."""
    id: int
    name: str
    ipmi_ip: str
    ipmi_user: str
    drac_version: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    status: str
    last_seen: Optional[str] = None
    added_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, server: Server) -> "ServerResponse":
        """Convert Server ORM object to response, handling datetime serialization."""
        return cls(
            id=server.id,
            name=server.name,
            ipmi_ip=server.ipmi_ip,
            ipmi_user=server.ipmi_user,
            drac_version=server.drac_version,
            model=server.model,
            serial=server.serial,
            status=server.status,
            last_seen=server.last_seen.isoformat() if server.last_seen else None,
            added_at=server.added_at.isoformat() if server.added_at else datetime.now().isoformat(),
        )


class PowerAction(BaseModel):
    """Request body for power control."""
    action: str = Field(
        ...,
        pattern="^(on|off|restart|shutdown|push_button)$",
        description="Power action: on, off, restart, shutdown, push_button",
    )


@router.get("/", response_model=list[ServerResponse])
async def list_servers(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List all managed Dell servers."""
    result = await session.execute(select(Server).order_by(Server.name))
    servers = result.scalars().all()
    return [ServerResponse.from_orm(s) for s in servers]


@router.post("/", response_model=ServerResponse, status_code=201)
async def add_server(
    data: ServerCreate,
    _user: User = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Add a new server to manage.

    The password is encrypted at rest using AES-256-CBC.
    Returns the created server with generated ID.
    """
    # Check for duplicate name
    existing = await session.execute(select(Server).where(Server.name == data.name))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Server '{data.name}' already exists")

    # Check for duplicate IP
    existing = await session.execute(select(Server).where(Server.ipmi_ip == data.ipmi_ip))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Server with iDRAC IP {data.ipmi_ip} already exists")

    server = Server(
        name=data.name,
        ipmi_ip=data.ipmi_ip,
        ipmi_user=data.ipmi_user,
        ipmi_password_enc=encrypt_plaintext(data.ipmi_password),
    )
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return ServerResponse.from_orm(server)


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get details for a specific server by ID."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return ServerResponse.from_orm(server)


@router.put("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: int,
    data: ServerUpdate,
    _user: User = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Update server details. Only provided fields are changed."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if data.name is not None:
        server.name = data.name
    if data.ipmi_ip is not None:
        server.ipmi_ip = data.ipmi_ip
    if data.ipmi_user is not None:
        server.ipmi_user = data.ipmi_user
    if data.ipmi_password is not None:
        server.ipmi_password_enc = encrypt_plaintext(data.ipmi_password)

    await session.commit()
    await session.refresh(server)
    return ServerResponse.from_orm(server)


@router.delete("/{server_id}", status_code=204)
async def delete_server(
    server_id: int,
    _user: User = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Remove a server and all its associated sensor data."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    await session.delete(server)
    await session.commit()


@router.post("/{server_id}/power", response_model=dict)
async def control_power(
    server_id: int,
    body: PowerAction,
    _user: User = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
):
    """Send power control command to server's iDRAC.

    Supported actions:
    - on: Power on the server
    - off: Force power off
    - restart: Force restart
    - shutdown: Graceful OS shutdown
    - push_button: Simulate power button press
    """
    from dsm.idrac_connector import IdracConnector, IdracConnectionError

    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    password = decrypt_ciphertext(server.ipmi_password_enc)
    if not password:
        raise HTTPException(status_code=500, detail="Cannot decrypt server credentials")

    # Map human-readable actions to Redfish ResetType values
    action_map = {
        "on": "On",
        "off": "ForceOff",
        "restart": "ForceRestart",
        "shutdown": "GracefulShutdown",
        "push_button": "PushPowerButton",
    }

    connector = IdracConnector(
        ip=server.ipmi_ip,
        username=server.ipmi_user,
        password=password,
    )

    try:
        success = await connector.set_power_state(action_map[body.action])
        await connector.close()
        if not success:
            raise HTTPException(status_code=500, detail="Power command failed")
        return {"status": "ok", "message": f"Power action '{body.action}' sent to {server.name}"}
    except IdracConnectionError as e:
        await connector.close()
        raise HTTPException(status_code=502, detail=f"Cannot connect to iDRAC: {e}")
