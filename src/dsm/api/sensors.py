"""Sensor data endpoints and WebSocket streaming.

Provides REST endpoints for querying historical sensor readings,
latest per-sensor snapshots, and per-server summaries, plus a
WebSocket endpoint for real-time push updates from the poller.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.database import get_session
from dsm.models import SensorReading, Server
from dsm.sensor_poller import SensorPoller

router = APIRouter(prefix="/sensors", tags=["sensors"])

# Shared poller instance — started/stopped by app lifespan
poller: SensorPoller = SensorPoller()


@router.get("/")
async def get_sensors(
    server_id: int = None,
    sensor_type: str = None,
    hours: float = 1.0,
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
async def get_sensor_summary(server_id: int, session: AsyncSession = Depends(get_session)):
    """Get current temperature summary for a server.

    Fetches the most recent readings (last 5 minutes) and returns
    the latest value per sensor label along with server metadata.
    """
    server = await session.get(Server, server_id)
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


@router.websocket("/ws/{server_id}")
async def sensor_websocket(websocket: WebSocket, server_id: int):
    """WebSocket endpoint for real-time sensor updates.

    Clients connect here and receive JSON messages pushed by the
    SensorPoller every polling cycle. The connection stays open
    until the client disconnects.
    """
    await websocket.accept()
    poller.add_websocket_client(websocket)

    try:
        while True:
            # Keep connection alive — just consume ping/control messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        poller.remove_websocket_client(websocket)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"WebSocket error: {e}")
        poller.remove_websocket_client(websocket)
