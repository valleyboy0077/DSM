"""Sensor polling service.

Periodically polls all registered servers for temperature and fan data,
stores readings in the database, and publishes via WebSocket.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.config import settings
from dsm.database import async_session
from dsm.fan_control import FanController
from dsm.idrac_connector import IdracConnector, IdracError
from dsm.models import FanConfig, FanMode, Server, ServerStatus, SensorReading, TempProfile, TempProfileRange

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
        self._manual_override_task: Optional[asyncio.Task] = None
        self._connectors: dict = {}  # server_id -> IdracConnector
        self._last_fan_control_at: dict[int, datetime] = {}
        # Remember the last fan percentage we successfully commanded per server.
        # Some iDRAC paths report stale PWM percentages while a manual override is
        # active, so the next thermal decision needs the commanded target rather
        # than trusting live telemetry alone.
        self._last_fan_control_target: dict[int, int] = {}
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
                drac_version=server.drac_version,
            )
        else:
            self._connectors[server.id].set_control_profile_hint(server.drac_version)
        return self._connectors[server.id]

    async def _load_active_profile_ranges(self, session: AsyncSession, server_id: int) -> list[Any]:
        profile_result = await session.execute(
            select(TempProfile).where(TempProfile.server_id == server_id, TempProfile.is_default == True)
        )
        profile = cast(Any, profile_result.scalars().first())
        if not profile:
            return []

        range_result = await session.execute(
            select(TempProfileRange).where(TempProfileRange.profile_id == profile.id)
        )
        return list(range_result.scalars().all())

    @staticmethod
    def _current_fan_percent_from_inventory(fans: list[Any], fallback: int) -> int:
        percents = [fan.percent for fan in fans if getattr(fan, "percent", None) is not None]
        if percents:
            return int(max(percents))
        return fallback

    async def _maybe_auto_control_fans(
        self,
        session: AsyncSession,
        server: Server,
        connector: IdracConnector,
        sensor_data,
    ) -> Optional[dict]:
        result = await session.execute(select(FanConfig).where(FanConfig.server_id == server.id))
        fan_config = cast(Any, result.scalars().first())
        if not fan_config or not fan_config.auto_control:
            return None

        profile_ranges = await self._load_active_profile_ranges(session, server.id)
        inventory_fan_percent = self._current_fan_percent_from_inventory(sensor_data.fans, fan_config.manual_speed)
        current_fan_percent = self._last_fan_control_target.get(server.id, inventory_fan_percent)
        controller = FanController(
            connector=connector,
            cpu_temp_min=fan_config.cpu_temp_min,
            cpu_temp_max=fan_config.cpu_temp_max,
            disk_temp_min=fan_config.disk_temp_min,
            disk_temp_max=fan_config.disk_temp_max,
            fan_min=7,
            fan_max=100,
        )
        controller._mode = FanMode.AUTO.value
        controller._current_fan_percent = current_fan_percent
        result = await controller.control_cycle(
            sensor_data=sensor_data,
            current_fan_percent=current_fan_percent,
            profile_ranges=profile_ranges,
            step_percent=3,
        )
        now = datetime.now(timezone.utc)
        if result.action_taken in {"increased", "decreased"}:
            self._last_fan_control_at[server.id] = now
            self._last_fan_control_target[server.id] = result.target_fan_percent
        return {
            "target_fan_percent": result.target_fan_percent,
            "action_taken": result.action_taken,
            "reason": result.reason,
            "current_fan_percent": current_fan_percent,
            "polling_seconds": settings.sensor_poll_interval,
        }

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
            # Re-fetch the server in this session so ORM updates are tracked
            db_server = await session.get(Server, server.id)
            if db_server is None:
                return {"status": "error", "error": f"Server {server.id} not found"}

            try:
                # Fetch all thermal + fan data from iDRAC
                sensor_data = await connector.get_sensors()

                # Update server status to online
                db_server.status = ServerStatus.ONLINE.value
                db_server.last_seen = datetime.now(timezone.utc)

                if sensor_data.system_info:
                    db_server.model = sensor_data.system_info.model or db_server.model
                    db_server.serial = sensor_data.system_info.service_tag or db_server.serial
                    db_server.drac_version = sensor_data.system_info.drac_version

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

                fan_control = await self._maybe_auto_control_fans(session, db_server, connector, sensor_data)

                result = {
                    "status": "ok",
                    "server": db_server.name,
                    "server_id": db_server.id,
                    "timestamp": db_server.last_seen.isoformat(),
                    "temperatures": [
                        {"name": t.name, "value": t.value_celsius, "context": t.physical_context}
                        for t in sensor_data.temperatures
                    ],
                    "fans": [
                        {"name": f.name, "rpm": f.rpm}
                        for f in sensor_data.fans
                    ],
                    "power_state": sensor_data.system_info.power_state if sensor_data.system_info else "Unknown",
                    "fan_control": fan_control,
                }

                logger.info(f"Polled {db_server.name}: {len(sensor_data.temperatures)} temps, {len(sensor_data.fans)} fans")
                return result

            except IdracError as e:
                db_server.status = ServerStatus.DEGRADED.value
                await session.commit()
                logger.warning(f"Poll error for {db_server.name}: {e}")
                return {"status": "error", "server": db_server.name, "error": str(e)}
            except Exception as e:
                db_server.status = ServerStatus.OFFLINE.value
                await session.commit()
                logger.error(f"Unexpected error polling {db_server.name}: {e}")
                return {"status": "error", "server": db_server.name, "error": str(e)}

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

    async def _enforce_manual_overrides_once(self):
        """Re-apply persisted manual overrides so transient controllers stay pinned.

        Some iDRAC7 paths (notably the R730xd via Dell OEM IPMI) accept an exact
        manual percentage immediately but let the override decay after ~20–30s
        unless it is refreshed. Keepalive re-application makes the persisted UI
        state match the real hardware state until the user switches back to auto.
        """
        async with async_session() as session:
            result = await session.execute(
                select(Server, FanConfig)
                .join(FanConfig, FanConfig.server_id == Server.id)
                .where(
                    FanConfig.auto_control.is_(False),
                    FanConfig.mode == FanMode.MANUAL.value,
                )
            )
            rows = result.all()

        for server, config in rows:
            connector = await self._get_connector(server)
            if connector is None:
                continue
            controller = FanController(connector=connector)
            success = await controller.set_manual_speed(config.manual_speed)
            if success:
                logger.info(
                    "Re-applied manual fan override for %s at %s%%",
                    server.name,
                    config.manual_speed,
                )
            else:
                logger.warning(
                    "Failed to re-apply manual fan override for %s at %s%%",
                    server.name,
                    config.manual_speed,
                )

    async def _refresh_auto_control_targets_once(self):
        """Re-assert low-speed auto targets on IPMI-backed iDRAC7 systems.

        Some IPMI fan-control paths accept the requested duty cycle but let it
        drift upward again if the manual target is not refreshed periodically.
        When auto control has already decided on a target and cached it in
        ``_last_fan_control_target``, keep sending that same target on a shorter
        cadence than the main sensor poll so cool systems can continue easing
        down toward the configured floor.
        """
        async with async_session() as session:
            result = await session.execute(
                select(Server, FanConfig)
                .join(FanConfig, FanConfig.server_id == Server.id)
                .where(
                    FanConfig.auto_control.is_(True),
                    FanConfig.mode == FanMode.AUTO.value,
                )
            )
            rows = result.all()

        for server, config in rows:
            target = self._last_fan_control_target.get(server.id)
            if target is None:
                continue
            connector = await self._get_connector(server)
            if connector is None:
                continue
            if getattr(connector, "_fan_control_backend", None) != "ipmi":
                continue
            controller = FanController(connector=connector, fan_min=7)
            controller._mode = FanMode.AUTO.value
            success = await controller._apply_fan_mode("Manual", target)
            if success:
                logger.info(
                    "Refreshed auto fan target for %s at %s%%",
                    server.name,
                    target,
                )
            else:
                logger.warning(
                    "Failed to refresh auto fan target for %s at %s%%",
                    server.name,
                    target,
                )

    async def _manual_override_loop(self):
        """Background keepalive for persisted manual overrides."""
        interval_seconds = 15
        while self._running:
            try:
                await self._enforce_manual_overrides_once()
                await self._refresh_auto_control_targets_once()
            except Exception as e:
                logger.error(f"Manual override loop error: {e}")

            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    async def start(self):
        """Start the polling loop as a background asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        self._manual_override_task = asyncio.create_task(self._manual_override_loop())
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
        if self._manual_override_task:
            self._manual_override_task.cancel()
            try:
                await self._manual_override_task
            except asyncio.CancelledError:
                pass
            self._manual_override_task = None
        for connector in self._connectors.values():
            try:
                await connector.close()
            except Exception:
                pass
        self._connectors.clear()
        logger.info("Sensor poller stopped")

    @staticmethod
    def _classify_sensor(sensor) -> str:
        """Classify a temperature sensor by type based on name/context.

        Returns one of: 'cpu', 'disk', 'ambient', 'power_supply', 'other'.
        """
        name_lower = (sensor.name or "").lower()
        context = (sensor.physical_context or "").lower()

        if "cpu" in name_lower or context == "cpu":
            return "cpu"
        if "disk" in name_lower or "drive" in name_lower or context == "drive":
            return "disk"
        if "inlet" in name_lower or "exhaust" in name_lower or "ambient" in name_lower:
            return "ambient"
        if "psu" in name_lower or "power" in name_lower:
            return "power_supply"
        return "other"
