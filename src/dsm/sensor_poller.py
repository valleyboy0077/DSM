"""Sensor polling service.

Periodically polls all registered servers for temperature and fan data,
stores readings in the database, and publishes via WebSocket.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.config import settings
from dsm.database import async_session
from dsm.idrac_connector import IdracConnector, IdracError
from dsm.models import FanConfig, Server, ServerStatus, SensorReading

logger = logging.getLogger(__name__)


class SensorPoller:
    """Polls all registered servers at configurable intervals.

    Each polling cycle opens its own DB session, queries all registered
    servers, fetches sensor data from each iDRAC, and stores readings.
    """

    def __init__(self):
        """Initialize the sensor poller."""
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._connectors: dict = {}  # server_id -> IdracConnector
        self._websocket_clients: list = []  # WebSocket for real-time updates

    def add_websocket_client(self, ws):
        """Register a WebSocket client for real-time sensor updates."""
        self._websocket_clients.append(ws)

    def remove_websocket_client(self, ws):
        """Unregister a WebSocket client."""
        if ws in self._websocket_clients:
            self._websocket_clients.remove(ws)

    async def _get_connector(self, server: Server) -> Optional[IdracConnector]:
        """Get or create an IdracConnector for a server.

        Caches connectors per server_id to avoid repeated instantiation.
        Decrypts the stored password using the crypto module.
        """
        if server.id not in self._connectors:
            from dsm.crypto import decrypt_ciphertext
            password = decrypt_ciphertext(server.ipmi_password_enc)
            if password is None:
                logger.error(f"Cannot decrypt password for server {server.name}")
                return None
            self._connectors[server.id] = IdracConnector(
                ip=server.ipmi_ip,
                username=server.ipmi_user,
                password=password,
            )
        return self._connectors[server.id]

    async def poll_server(self, server: Server) -> dict:
        """Poll a single server and store readings in the database.

        Opens a new DB session, fetches sensor data from the iDRAC,
        updates the server status, and stores temperature readings.

        Returns:
            dict with status, server name, temperatures, fans, and power state.
        """
        connector = await self._get_connector(server)
        if connector is None:
            return {"status": "error", "error": "Cannot decrypt credentials"}

        async with async_session() as session:
            try:
                # Fetch all thermal + fan data from iDRAC
                sensor_data = await connector.get_sensors()

                # Update server status to online
                server.status = ServerStatus.ONLINE.value
                server.last_seen = datetime.now(timezone.utc)

                if sensor_data.system_info:
                    server.model = sensor_data.system_info.model or server.model
                    server.serial = sensor_data.system_info.service_tag or server.serial
                    server.drac_version = sensor_data.system_info.drac_version

                # Store each temperature reading
                for temp in sensor_data.temperatures:
                    reading = SensorReading(
                        server_id=server.id,
                        sensor_type=self._classify_sensor(temp),
                        sensor_label=temp.name,
                        value=temp.value_celsius,
                    )
                    session.add(reading)

                await session.commit()

                result = {
                    "status": "ok",
                    "server": server.name,
                    "server_id": server.id,
                    "timestamp": server.last_seen.isoformat(),
                    "temperatures": [
                        {"name": t.name, "value": t.value_celsius, "context": t.physical_context}
                        for t in sensor_data.temperatures
                    ],
                    "fans": [
                        {"name": f.name, "rpm": f.rpm}
                        for f in sensor_data.fans
                    ],
                    "power_state": sensor_data.system_info.power_state if sensor_data.system_info else "Unknown",
                }

                logger.info(f"Polled {server.name}: {len(sensor_data.temperatures)} temps, {len(sensor_data.fans)} fans")
                return result

            except IdracError as e:
                server.status = ServerStatus.DEGRADED.value
                await session.commit()
                logger.warning(f"Poll error for {server.name}: {e}")
                return {"status": "error", "server": server.name, "error": str(e)}
            except Exception as e:
                server.status = ServerStatus.OFFLINE.value
                await session.commit()
                logger.error(f"Unexpected error polling {server.name}: {e}")
                return {"status": "error", "server": server.name, "error": str(e)}

    async def poll_all(self) -> list:
        """Poll all registered servers.

        Queries the database for all Server records, then polls each
        one sequentially. Results are broadcast to WebSocket clients.

        Returns:
            List of result dicts, one per server.
        """
        async with async_session() as session:
            result = select(Server)
            servers = (await session.execute(result)).scalars().all()

        results = []
        for server in servers:
            result = await self.poll_server(server)
            results.append(result)

            # Broadcast to connected WebSocket clients
            await self._broadcast(result)

        return results

    async def _broadcast(self, data: dict):
        """Send JSON data to all connected WebSocket clients.

        Removes dead clients from the list on send failure.
        """
        import json
        message = json.dumps(data)
        dead_clients = []

        for ws in self._websocket_clients:
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.warning(f"WebSocket send failed: {e}")
                dead_clients.append(ws)

        for ws in dead_clients:
            self.remove_websocket_client(ws)

    async def _poll_loop(self):
        """Main polling loop — runs while self._running is True."""
        while self._running:
            try:
                await self.poll_all()
            except Exception as e:
                logger.error(f"Poll loop error: {e}")

            # Sleep until next interval
            try:
                await asyncio.sleep(settings.sensor_poll_interval)
            except asyncio.CancelledError:
                break

    async def start(self):
        """Start the polling loop as a background asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"Sensor poller started (interval: {settings.sensor_poll_interval}s)")

    async def stop(self):
        """Cancel and await the polling loop task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Sensor poller stopped")

    @staticmethod
    def _classify_sensor(sensor) -> str:
        """Classify a temperature sensor by type based on name/context.

        Returns one of: 'cpu', 'disk', 'ambient', 'power_supply', 'other'.
        """
        name_lower = sensor.name.lower()
        context = sensor.physical_context.lower()

        if "cpu" in name_lower or context == "cpu":
            return "cpu"
        if "disk" in name_lower or "drive" in name_lower or context == "drive":
            return "disk"
        if "inlet" in name_lower or "exhaust" in name_lower or "ambient" in name_lower:
            return "ambient"
        if "psu" in name_lower or "power" in name_lower:
            return "power_supply"
        return "other"
