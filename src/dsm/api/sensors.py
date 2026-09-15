"""Sensor data endpoints and WebSocket streaming.

Provides REST endpoints for querying historical sensor readings,
latest per-sensor snapshots, and per-server summaries, plus a
WebSocket endpoint for real-time push updates from the poller.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.api.idrac import execute_idrac_request
from dsm.auth import decode_token, get_current_user
from dsm.config import settings
from dsm.database import async_session, get_session
from dsm.models import SensorReading, Server, User
from dsm.sensor_poller import SensorPoller

router = APIRouter(prefix="/sensors", tags=["sensors"])

# Shared poller instance — started/stopped by app lifespan
poller: SensorPoller = SensorPoller()


async def _database_dashboard_snapshot(server: Server, session: AsyncSession) -> dict[str, Any]:
    """Build the cold-start fallback while the poller's memory cache is empty."""
    latest_timestamps = (
        select(
            SensorReading.server_id,
            SensorReading.sensor_label,
            func.max(SensorReading.timestamp).label("max_ts"),
        )
        .where(SensorReading.server_id == server.id)
        .group_by(SensorReading.server_id, SensorReading.sensor_label)
        .subquery()
    )
    result = await session.execute(
        select(SensorReading)
        .join(
            latest_timestamps,
            (SensorReading.server_id == latest_timestamps.c.server_id)
            & SensorReading.sensor_label.is_not_distinct_from(latest_timestamps.c.sensor_label)
            & (SensorReading.timestamp == latest_timestamps.c.max_ts),
        )
    )
    latest: dict[str, SensorReading] = {}
    for reading in result.scalars().all():
        if reading.sensor_label not in latest:
            latest[reading.sensor_label] = reading
    readings = [
        {
            "label": reading.sensor_label,
            "type": reading.sensor_type,
            "value": reading.value,
            "timestamp": reading.timestamp.isoformat() if reading.timestamp else None,
        }
        for reading in latest.values()
    ]
    captured_at = max((reading.timestamp for reading in latest.values() if reading.timestamp), default=None)
    if captured_at is not None and captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    captured_at_iso = captured_at.isoformat() if captured_at is not None else None
    age_seconds = (
        max(0.0, round((datetime.now(timezone.utc) - captured_at).total_seconds(), 3))
        if captured_at is not None
        else None
    )
    return {
        "server_id": server.id,
        "server_name": server.name,
        "status": server.status,
        "revision": 0,
        # A restarted poller cannot know the revision/cycle that wrote a DB
        # row, so do not attribute persisted data to its current cycle.
        "cycle_id": 0,
        "freshness": {
            "source": "database" if captured_at_iso else "unavailable",
            "captured_at": captured_at_iso,
            "age_seconds": age_seconds,
            "stale": age_seconds is None or age_seconds > settings.sensor_poll_interval * 2,
            "last_error": None,
            "last_attempt_at": None,
        },
        "readings": readings,
        "fans": [],
    }


@router.get("/dashboard-snapshot")
@router.get("/dashboard", include_in_schema=False)
async def get_dashboard_snapshot(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Return cache-first normalized telemetry for all currently registered servers."""
    servers = (await session.execute(select(Server).order_by(Server.id))).scalars().all()
    snapshots = []
    for server in servers:
        snapshots.append(poller.get_cached_snapshot(server.id) or await _database_dashboard_snapshot(server, session))
    return {"servers": snapshots, "poll_cycle": poller.get_dashboard_snapshot()["poll_cycle"]}


async def _authenticate_websocket(websocket: WebSocket) -> bool:
    """Authenticate a browser WebSocket with the same JWT issued at login.

    Browsers cannot attach the API's Authorization header to a native
    WebSocket constructor, so the dashboard supplies its login JWT in the
    ``token`` query parameter.  It is validated and checked against the user
    table before the socket is accepted.
    """
    authorization = websocket.headers.get("authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
    token = token or websocket.query_params.get("token") or websocket.query_params.get("access_token")
    if not token:
        await websocket.close(code=1008)
        return False
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (HTTPException, KeyError, TypeError, ValueError):
        await websocket.close(code=1008)
        return False

    async with async_session() as session:
        user = await session.get(User, user_id)
    if not user or not user.is_active:
        await websocket.close(code=1008)
        return False
    return True


@router.get("/")
async def get_sensors(
    server_id: int = None,
    sensor_type: str = None,
    hours: float = 1.0,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get sensor readings with optional filters.

    Query params:
        server_id: Filter by server ID
        sensor_type: Filter by type (cpu, disk, ambient, power_supply)
        hours: Time range in hours (default: 1)

    Returns:
        List of readings with count metadata.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    query = (
        select(SensorReading)
        .where(SensorReading.timestamp >= cutoff)
        .order_by(SensorReading.timestamp.desc())
    )

    if server_id:
        query = query.where(SensorReading.server_id == server_id)
    if sensor_type:
        query = query.where(SensorReading.sensor_type == sensor_type)

    result = await session.execute(query)
    readings = result.scalars().all()

    return {
        "count": len(readings),
        "readings": [
            {
                "id": r.id,
                "server_id": r.server_id,
                "sensor_type": r.sensor_type,
                "sensor_label": r.sensor_label,
                "value": r.value,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in readings
        ],
    }


@router.get("/latest")
async def get_latest_sensors(
    server_id: int = None,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get the latest reading for each sensor label on a server.

    Uses a subquery to find the most recent timestamp per (server, label),
    then joins back to fetch the full reading rows.
    """
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id is required")

    subquery = (
        select(
            SensorReading.server_id,
            SensorReading.sensor_label,
            func.max(SensorReading.timestamp).label("max_ts"),
        )
        .where(SensorReading.server_id == server_id)
        .group_by(SensorReading.server_id, SensorReading.sensor_label)
        .subquery()
    )

    query = (
        select(SensorReading)
        .join(
            subquery,
            (SensorReading.server_id == subquery.c.server_id)
            & (SensorReading.sensor_label == subquery.c.sensor_label)
            & (SensorReading.timestamp == subquery.c.max_ts),
        )
    )

    result = await session.execute(query)
    readings = result.scalars().all()

    # Group by sensor category
    categorized = {"cpu": [], "disk": [], "ambient": [], "other": []}
    for r in readings:
        bucket = r.sensor_type if r.sensor_type in categorized else "other"
        categorized[bucket].append({
            "label": r.sensor_label,
            "value": r.value,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        })

    return {
        "server_id": server_id,
        "categories": categorized,
    }


@router.get("/summary/{server_id}")
async def get_sensor_summary(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get current temperature summary for a server.

    Fetches the most recent readings (last 5 minutes) and returns
    the latest value per sensor label along with server metadata.
    """
    server = cast(Any, await session.get(Server, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = await session.execute(
        select(SensorReading)
        .where(
            SensorReading.server_id == server_id,
            SensorReading.timestamp >= cutoff,
        )
        .order_by(SensorReading.timestamp.desc())
    )
    readings = result.scalars().all()

    # Deduplicate: keep first (most recent) per sensor label
    latest: dict = {}
    for r in readings:
        if r.sensor_label not in latest:
            latest[r.sensor_label] = {
                "label": r.sensor_label,
                "type": r.sensor_type,
                "value": r.value,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }

    return {
        "server_id": server.id,
        "server_name": server.name,
        "status": server.status,
        "last_seen": server.last_seen.isoformat() if server.last_seen else None,
        "sensors": list(latest.values()),
    }


@router.get("/live/{server_id}")
async def get_live_sensors(
    server_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get a live sensor snapshot directly from iDRAC.

    Returns the current temperatures and fan readings from hardware rather than
    the stored sensor history tables.
    """
    server = cast(Any, await session.get(Server, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    sensor_data = await execute_idrac_request(
        server,
        lambda connector: connector.get_sensors(),
        credential_error_detail="Cannot decrypt credentials",
        connection_error_detail=lambda error: f"Cannot connect to iDRAC: {error}",
        idrac_error_detail=lambda error: f"iDRAC sensor query failed: {error}",
    )
    system = sensor_data.system_info
    return {
            "server_id": server.id,
            "server_name": server.name,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "source": "idrac",
            "system_info": None
            if system is None
            else {
                "model": system.model,
                "service_tag": system.service_tag,
                "bios_version": system.bios_version,
                "firmware_version": system.firmware_version,
                "drac_version": system.drac_version,
                "power_state": system.power_state,
            },
            "temperatures": [
                {
                    "name": temp.name,
                    "value_celsius": temp.value_celsius,
                    "physical_context": temp.physical_context,
                    "upper_critical": temp.upper_critical,
                    "upper_warning": temp.upper_warning,
                }
                for temp in sensor_data.temperatures
            ],
            "fans": [
                {
                    "name": fan.name,
                    "member_id": fan.member_id,
                    "rpm": fan.rpm,
                    "percent": fan.percent,
                    "health": fan.health,
                }
                for fan in sensor_data.fans
            ],
    }


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    """Authenticated fleet telemetry stream for the dashboard."""
    if not await _authenticate_websocket(websocket):
        return
    await websocket.accept()
    poller.add_dashboard_websocket_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Dashboard WebSocket error: %s", exc)
    finally:
        poller.remove_websocket_client(websocket)


@router.websocket("/ws/{server_id}")
async def sensor_websocket(websocket: WebSocket, server_id: int):
    """WebSocket endpoint for real-time sensor updates.

    Clients connect here and receive JSON messages pushed by the
    SensorPoller every polling cycle. The connection stays open
    until the client disconnects.
    """
    if not await _authenticate_websocket(websocket):
        return
    await websocket.accept()
    poller.add_websocket_client(websocket, server_id=server_id)

    try:
        while True:
            # Keep connection alive — just consume ping/control messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"WebSocket error: {e}")
    finally:
        poller.remove_websocket_client(websocket)
