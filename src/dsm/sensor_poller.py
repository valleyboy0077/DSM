"""Sensor polling service.

Periodically polls all registered servers for temperature and fan data,
stores readings in the database, and publishes via WebSocket.
"""

import asyncio
import copy
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dsm.config import settings
from dsm.database import async_session
from dsm.fan_control import FanController, FanControlTuning
from dsm.idrac_connector import IdracConnector, IdracError
from dsm.models import FanConfig, FanMode, SensorReading, Server, ServerStatus
from dsm.temp_profile_repository import get_active_temp_profile_ranges

logger = logging.getLogger(__name__)


class SensorPoller:
    """Polls all registered servers at configurable intervals.

    Each polling cycle opens its own DB session, queries all registered
    servers, fetches sensor data from each iDRAC, and stores readings.
    """

    def __init__(self, monotonic_clock: Callable[[], float] = time.monotonic):
        """Initialize the sensor poller."""
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._manual_override_task: Optional[asyncio.Task] = None
        self._connectors: dict = {}  # server_id -> IdracConnector
        # Automatic cadence must not be affected by wall-clock adjustments.
        self._last_fan_control_at: dict[int, float] = {}
        self._last_auto_refresh_at: dict[int, float] = {}
        self._monotonic_clock = monotonic_clock
        # Last two monotonic-time readings per server, keyed by physical sensor.
        self._temperature_history: dict[int, deque[tuple[float, dict[str, float]]]] = {}
        # Last fan percentage successfully commanded per server.  This is the
        # automatic control baseline once present; telemetry is not an
        # acknowledgement and can lag a manual command.
        self._last_fan_control_target: dict[int, int] = {}
        # Latest usable controller-reported duty, retained separately from the
        # command target for telemetry and diagnostics.
        self._last_observed_fan_percent: dict[int, int] = {}
        # Keep fleet dashboard clients separate from the legacy server streams.
        # A fleet event must never be fanned out through a server-scoped URL.
        self._dashboard_websocket_clients: list = []
        self._server_websocket_clients: dict[int, list] = {}
        # The dashboard reads this process-local cache first.  It deliberately
        # holds normalized, JSON-ready values rather than ORM rows so a failed
        # poll can retain the last known-good telemetry without touching the
        # persisted history.
        self._snapshot_cache: dict[int, dict[str, Any]] = {}
        self._snapshot_revisions: dict[int, int] = {}
        self._poll_all_lock = asyncio.Lock()
        self._poll_cycle: dict[str, Any] = {
            "cycle_id": 0,
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "next_poll_at": None,
        }

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _isoformat(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value is not None else None

    def get_cached_snapshot(self, server_id: int) -> Optional[dict[str, Any]]:
        """Return a defensive copy of one normalized live snapshot."""
        snapshot = self._snapshot_cache.get(server_id)
        return self._snapshot_with_current_age(snapshot) if snapshot is not None else None

    def get_dashboard_snapshot(self) -> dict[str, Any]:
        """Return cache contents and lifecycle data for a cache-first client."""
        return {
            "servers": [self._snapshot_with_current_age(self._snapshot_cache[key]) for key in sorted(self._snapshot_cache)],
            "poll_cycle": copy.deepcopy(self._poll_cycle),
        }

    def _snapshot_with_current_age(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(snapshot)
        freshness = snapshot["freshness"]
        age = self._age_seconds(freshness.get("captured_at"), self._utcnow())
        freshness["age_seconds"] = age
        if age is not None and age > settings.sensor_poll_interval * 2:
            freshness["stale"] = True
        return snapshot

    @staticmethod
    def _normalize_readings(result: dict[str, Any]) -> list[dict[str, Any]]:
        timestamp = result.get("timestamp")
        return [
            {
                "label": item.get("name"),
                "type": item.get("type", "other"),
                "value": item.get("value"),
                "timestamp": timestamp,
            }
            for item in result.get("temperatures", [])
        ]

    @staticmethod
    def _normalize_fans(result: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "name": item.get("name"),
                "member_id": item.get("member_id") or item.get("name"),
                "rpm": item.get("rpm"),
                "percent": item.get("percent"),
                "percent_source": item.get("percent_source", "unavailable"),
                "health": item.get("health"),
                "source": "idrac",
            }
            for item in result.get("fans", [])
        ]

    def _cache_poll_result(self, result: dict[str, Any], cycle_id: int, attempted_at: datetime) -> dict[str, Any]:
        """Merge a poll outcome into the live cache without losing good data."""
        server_id = result.get("server_id")
        if not isinstance(server_id, int):
            return result

        previous = self._snapshot_cache.get(server_id)
        revision = self._snapshot_revisions.get(server_id, 0) + 1
        self._snapshot_revisions[server_id] = revision
        success = result.get("status") == "ok"
        captured_at = result.get("timestamp") if success else (previous or {}).get("freshness", {}).get("captured_at")
        snapshot = {
            "server_id": server_id,
            "server_name": result.get("server") or (previous or {}).get("server_name") or f"Server {server_id}",
            "status": result.get("status") if not success else "online",
            "revision": revision,
            "cycle_id": cycle_id,
            "freshness": {
                "source": "memory" if success or previous else "unavailable",
                "captured_at": captured_at,
                "age_seconds": 0.0 if success else self._age_seconds(captured_at, attempted_at),
                "stale": not success,
                "last_error": None if success else result.get("error", "Polling failed"),
                "last_attempt_at": self._isoformat(attempted_at),
            },
            "readings": self._normalize_readings(result) if success else copy.deepcopy((previous or {}).get("readings", [])),
            "fans": self._normalize_fans(result) if success else copy.deepcopy((previous or {}).get("fans", [])),
        }
        self._snapshot_cache[server_id] = snapshot
        return snapshot

    @staticmethod
    def _age_seconds(captured_at: Optional[str], now: datetime) -> Optional[float]:
        if not captured_at:
            return None
        try:
            captured = datetime.fromisoformat(captured_at)
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            return max(0.0, round((now - captured).total_seconds(), 3))
        except (TypeError, ValueError):
            return None

    def clear_auto_control_state(self, server_id: int) -> None:
        """Forget automatic-control state after an operator mode transition."""
        self._last_fan_control_target.pop(server_id, None)
        self._last_observed_fan_percent.pop(server_id, None)
        self._last_fan_control_at.pop(server_id, None)
        self._last_auto_refresh_at.pop(server_id, None)
        self._temperature_history.pop(server_id, None)

    def add_dashboard_websocket_client(self, ws) -> None:
        """Register a client for the authenticated fleet dashboard stream."""
        if ws not in self._dashboard_websocket_clients:
            self._dashboard_websocket_clients.append(ws)

    def add_websocket_client(self, ws, server_id: Optional[int] = None) -> None:
        """Register a legacy server-scoped client.

        ``server_id`` is deliberately required here so callers cannot create a
        second, unauthenticated global broadcast channel by accident.
        """
        if server_id is None:
            raise ValueError("server_id is required for the legacy sensor stream")
        clients = self._server_websocket_clients.setdefault(server_id, [])
        if ws not in clients:
            clients.append(ws)

    def remove_websocket_client(self, ws):
        """Unregister a WebSocket client."""
        while ws in self._dashboard_websocket_clients:
            self._dashboard_websocket_clients.remove(ws)
        for server_id, clients in list(self._server_websocket_clients.items()):
            while ws in clients:
                clients.remove(ws)
            if not clients:
                self._server_websocket_clients.pop(server_id, None)

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

    @staticmethod
    def _current_fan_percent_from_inventory(fans: list[Any]) -> Optional[int]:
        percents = []
        for fan in fans:
            percent = getattr(fan, "percent", None)
            source = getattr(fan, "percent_source", "pwm")
            if source not in {"pwm", "controller_percentage"}:
                continue
            try:
                percent = int(percent)
            except (TypeError, ValueError):
                continue
            if 0 <= percent <= 100:
                percents.append(percent)
        if percents:
            return int(max(percents))
        return None

    @staticmethod
    def _fan_control_tuning() -> FanControlTuning:
        return FanControlTuning(
            rise_gain_percent_per_c=settings.fan_control_rise_gain_percent_per_c,
            rise_rate_gain_percent_per_c_per_sec=settings.fan_control_rise_rate_gain_percent_per_c_per_sec,
            fall_gain_percent_per_c=settings.fan_control_fall_gain_percent_per_c,
            rise_activation_margin_c=settings.fan_control_rise_activation_margin_c,
            temp_deadband_c=settings.fan_control_temp_deadband_c,
            rate_deadband_c_per_sec=settings.fan_control_rate_deadband_c_per_sec,
            max_rise_step_percent=settings.fan_control_max_rise_step_percent,
            max_fall_step_percent=settings.fan_control_max_fall_step_percent,
        )

    def _temperature_rates(
        self,
        server_id: int,
        temperatures: list[Any],
        now: Optional[datetime] = None,
        monotonic_now: Optional[float] = None,
    ) -> dict[str, float]:
        """Calculate per-sensor °C/s from monotonic server-local history.

        ``now`` remains accepted for caller compatibility (and is still used
        by the command-dwell path); derivative elapsed time deliberately comes
        from a monotonic clock.
        """
        current = {
            FanController.sensor_key(sensor): float(sensor.value_celsius)
            for sensor in temperatures
            if FanController._classify_temp(sensor) in {"cpu", "disk"}
        }
        sample_at = self._monotonic_clock() if monotonic_now is None else monotonic_now
        history = self._temperature_history.setdefault(server_id, deque(maxlen=2))
        rates: dict[str, float] = {}
        if history:
            previous_at, previous = history[-1]
            elapsed = sample_at - previous_at
            if 0 < elapsed <= settings.fan_control_max_temperature_sample_gap_seconds:
                rates = {
                    key: (value - previous[key]) / elapsed
                    for key, value in current.items() if key in previous
                }
        history.append((sample_at, current))
        return rates

    @staticmethod
    def _is_emergency_cpu_rise(
        controller: FanController,
        temperatures: list[Any],
        profile_ranges: list[Any],
        temperature_rates: dict[str, float],
    ) -> bool:
        """Return whether CPU heat may safely bypass the normal command dwell.

        A controller-reported CPU temperature must be substantially above its
        resolved limit, or its monotonic derivative must show a very rapid
        positive rise.  The derivative is already discarded after an invalid
        or overlong sample gap by ``_temperature_rates``.
        """
        for sensor in temperatures:
            if FanController._classify_temp(sensor) != "cpu":
                continue
            _, maximum = controller._thresholds_for_sensor(sensor, profile_ranges)
            rate = temperature_rates.get(FanController.sensor_key(sensor), 0.0)
            if (
                sensor.value_celsius >= maximum + settings.fan_control_emergency_cpu_overtemp_c
                or rate >= settings.fan_control_emergency_cpu_rate_c_per_sec
            ):
                return True
        return False

    async def _maybe_auto_control_fans(
        self,
        session: AsyncSession,
        server: Server,
        connector: IdracConnector,
        sensor_data,
        now: Optional[datetime] = None,
        monotonic_now: Optional[float] = None,
    ) -> Optional[dict]:
        result = await session.execute(select(FanConfig).where(FanConfig.server_id == server.id))
        fan_config = cast(Any, result.scalars().first())
        if not fan_config or not fan_config.auto_control:
            return None

        # ``now`` is retained for call compatibility.  All elapsed-time
        # decisions below use the injectable monotonic clock instead.
        now = now or datetime.now(timezone.utc)
        command_now = self._monotonic_clock() if monotonic_now is None else monotonic_now
        profile_ranges = await get_active_temp_profile_ranges(session, server.id)
        temperature_rates = self._temperature_rates(
            server.id, sensor_data.temperatures, now, monotonic_now=command_now
        )
        inventory_fan_percent = self._current_fan_percent_from_inventory(sensor_data.fans)
        if inventory_fan_percent is not None:
            self._last_observed_fan_percent[server.id] = inventory_fan_percent
        # A successful command is normally the control baseline until it is
        # replaced by another successful command.  iDRAC PWM telemetry may be
        # stale while a manual target is taking effect, so cool/steady control
        # retains that authority.  A hot or rising cycle is different: a
        # higher usable controller PWM is a safety floor and must never be
        # followed by a lower manual command.
        current_fan_percent = (
            self._last_fan_control_target.get(server.id)
            if server.id in self._last_fan_control_target
            else inventory_fan_percent
        )
        if current_fan_percent is None:
            logger.warning(
                "Skipping DSM fan adjustment for %s: no reported PWM and no prior commanded target",
                server.name,
            )
            return None
        controller = FanController(
            connector=connector,
            cpu_temp_min=fan_config.cpu_temp_min,
            cpu_temp_max=fan_config.cpu_temp_max,
            disk_temp_min=fan_config.disk_temp_min,
            disk_temp_max=fan_config.disk_temp_max,
            fan_min=7,
            fan_max=100,
            tuning=self._fan_control_tuning(),
        )
        current_fan_percent = controller._clamp_fan_percent(current_fan_percent)
        controller._mode = FanMode.AUTO.value
        controller._current_fan_percent = current_fan_percent
        result = await controller.control_cycle(
            sensor_data=sensor_data,
            current_fan_percent=current_fan_percent,
            profile_ranges=profile_ranges,
            # ``control_cycle`` keeps its legacy 3% default for direct
            # callers.  The production poller uses its configured bounded
            # rise limit instead.
            step_percent=None,
            temperature_rates=temperature_rates,
            apply=False,
        )
        if (
            result.action_taken == "increased"
            and inventory_fan_percent is not None
            and inventory_fan_percent > current_fan_percent
        ):
            # The first pass established that the thermal policy requires a
            # rise.  Recalculate from the observed duty so the command is at
            # least the current hardware PWM, while retaining the cached
            # command as authority for cool or steady cycles.
            current_fan_percent = inventory_fan_percent
            controller._current_fan_percent = current_fan_percent
            result = await controller.control_cycle(
                sensor_data=sensor_data,
                current_fan_percent=current_fan_percent,
                profile_ranges=profile_ranges,
                step_percent=None,
                temperature_rates=temperature_rates,
                apply=False,
            )
        emergency_rise = (
            result.action_taken == "increased"
            and self._is_emergency_cpu_rise(
                controller,
                sensor_data.temperatures,
                profile_ranges,
                temperature_rates,
            )
        )
        # Polling can be much faster than a chassis can react.  Do not issue
        # another automatic command until the configured dwell has elapsed,
        # except for a severe or rapidly rising CPU temperature.
        last_command = self._last_fan_control_at.get(server.id)
        if (
            result.action_taken in {"increased", "decreased"}
            and not emergency_rise
            and last_command is not None
            and command_now - last_command < settings.fan_control_min_command_interval_seconds
        ):
            result.target_fan_percent = current_fan_percent
            result.action_taken = "unchanged"
            result.reason = "Minimum automatic command interval active; holding fan target"
        if result.action_taken in {"increased", "decreased"}:
            success = await controller._apply_fan_mode("Manual", result.target_fan_percent)
            if success:
                self._last_fan_control_at[server.id] = command_now
                self._last_fan_control_target[server.id] = result.target_fan_percent
            else:
                result.action_taken = "unchanged"
                result.target_fan_percent = current_fan_percent
                result.reason = "Failed to apply automatic fan target; holding previous target"
        return {
            "target_fan_percent": result.target_fan_percent,
            "action_taken": result.action_taken,
            "reason": result.reason,
            "current_fan_percent": current_fan_percent,
            "polling_seconds": settings.sensor_poll_interval,
            "temperature_rates_c_per_sec": temperature_rates,
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
            return {
                "status": "error",
                "server": server.name,
                "server_id": server.id,
                "error": "Cannot decrypt credentials",
            }

        async with async_session() as session:
            # Re-fetch the server in this session so ORM updates are tracked
            db_server = await session.get(Server, server.id)
            if db_server is None:
                return {
                    "status": "error",
                    "server": getattr(server, "name", f"Server {server.id}"),
                    "server_id": server.id,
                    "error": f"Server {server.id} not found",
                }

            try:
                # Fetch all thermal + fan data from iDRAC
                sensor_data = await connector.get_sensors()

                power_state = (
                    getattr(sensor_data.system_info, "power_state", None)
                    if sensor_data.system_info
                    else None
                )
                if isinstance(power_state, str) and power_state.strip().lower() == "off":
                    # A reachable iDRAC can still report a powered-off host.
                    # Treat this as a failed telemetry poll so the cache keeps
                    # last-known readings and exposes them as stale.
                    db_server.status = ServerStatus.OFFLINE.value
                    await session.commit()
                    error = "Server is powered off"
                    logger.info("Poll skipped for %s: %s", db_server.name, error)
                    return {
                        "status": "error",
                        "server": db_server.name,
                        "server_id": db_server.id,
                        "error": error,
                    }

                # Update server status to online
                db_server.status = ServerStatus.ONLINE.value
                db_server.last_seen = datetime.now(timezone.utc)

                if sensor_data.system_info:
                    db_server.model = sensor_data.system_info.model or db_server.model
                    db_server.serial = sensor_data.system_info.service_tag or db_server.serial
                    if db_server.drac_version not in {"idrac7", "idrac8"}:
                        db_server.drac_version = sensor_data.system_info.drac_version

                # Powered-off systems can report temperature inventory entries
                # without numeric values.  Keep those entries out of both the
                # NOT NULL persistence column and downstream fan calculations.
                sensor_data.temperatures = [
                    temp for temp in sensor_data.temperatures if temp.value_celsius is not None
                ]

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
                        {
                            "name": t.name,
                            "type": self._classify_sensor(t),
                            "value": t.value_celsius,
                            "context": t.physical_context,
                        }
                        for t in sensor_data.temperatures
                    ],
                    "fans": [
                        {
                            "name": f.name,
                            "member_id": f.member_id,
                            "rpm": f.rpm,
                            "percent": f.percent,
                            "percent_source": getattr(f, "percent_source", "unavailable"),
                            "health": f.health,
                        }
                        for f in sensor_data.fans
                    ],
                    "power_state": sensor_data.system_info.power_state if sensor_data.system_info else "Unknown",
                    "fan_control": fan_control,
                }

                logger.info(f"Polled {db_server.name}: {len(sensor_data.temperatures)} temps, {len(sensor_data.fans)} fans")
                return result

            except IdracError as e:
                await session.rollback()
                db_server.status = ServerStatus.DEGRADED.value
                await session.commit()
                logger.warning(f"Poll error for {db_server.name}: {e}")
                return {"status": "error", "server": db_server.name, "server_id": db_server.id, "error": str(e)}
            except Exception as e:
                await session.rollback()
                db_server.status = ServerStatus.OFFLINE.value
                await session.commit()
                logger.error(f"Unexpected error polling {db_server.name}: {e}")
                return {"status": "error", "server": db_server.name, "server_id": db_server.id, "error": str(e)}

    async def poll_all(self) -> list:
        """Poll all registered servers.

        Queries the database for all Server records, then polls a bounded
        number concurrently.  Calls are serialized so a slow cycle never
        overlaps the next one.

        Returns:
            List of result dicts, one per server.
        """
        async with self._poll_all_lock:
            started_at = self._utcnow()
            started_monotonic = self._monotonic_clock()
            cycle_id = int(self._poll_cycle["cycle_id"]) + 1
            self._poll_cycle = {
                "cycle_id": cycle_id,
                "status": "running",
                "started_at": self._isoformat(started_at),
                "completed_at": None,
                "duration_ms": None,
                "next_poll_at": None,
            }
            results: list[dict[str, Any]] = []
            tasks: list[asyncio.Task[dict]] = []
            cycle_cancelled = False
            try:
                async with async_session() as session:
                    servers = (await session.execute(select(Server))).scalars().all()

                semaphore = asyncio.Semaphore(settings.sensor_poll_concurrency)

                async def bounded_poll(server: Server) -> dict:
                    async with semaphore:
                        return await self.poll_server(server)

                tasks = [asyncio.create_task(bounded_poll(server)) for server in servers]
                for completed in asyncio.as_completed(tasks):
                    try:
                        result = await completed
                    except Exception as exc:  # defensive: poll_server normally contains failures
                        logger.exception("Unhandled per-server poll failure: %s", exc)
                        result = {"status": "error", "error": str(exc)}
                    results.append(result)
                    snapshot = self._cache_poll_result(result, cycle_id, self._utcnow())
                    if snapshot is not result:
                        # Event freshness is evaluated at emission time, just
                        # like a snapshot read, rather than retaining the
                        # provisional zero-age value from cache insertion.
                        await self._broadcast_snapshot(self._snapshot_with_current_age(snapshot), cycle_id)

                return results
            except asyncio.CancelledError:
                cycle_cancelled = True
                self._poll_cycle["status"] = "cancelled"
                raise
            except Exception:
                self._poll_cycle["status"] = "failed"
                raise
            finally:
                pending_tasks = [task for task in tasks if not task.done()]
                for task in pending_tasks:
                    task.cancel()
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)

                completed_at = self._utcnow()
                duration_seconds = max(0.0, self._monotonic_clock() - started_monotonic)
                self._poll_cycle.update({
                    "status": "completed" if self._poll_cycle["status"] == "running" else self._poll_cycle["status"],
                    "completed_at": self._isoformat(completed_at),
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "next_poll_at": self._isoformat(completed_at + timedelta(seconds=max(0.0, settings.sensor_poll_interval - duration_seconds))),
                })
                if not cycle_cancelled:
                    await self._broadcast({
                        "event": "poll.cycle.completed",
                        "event_type": "poll.cycle.completed",
                        "emitted_at": self._isoformat(completed_at),
                        "cycle_id": cycle_id,
                        "data": copy.deepcopy(self._poll_cycle),
                    })

    async def _broadcast(self, data: dict):
        """Send JSON data to all connected WebSocket clients.

        Removes dead clients from the list on send failure.
        """
        import json
        message = json.dumps(data)
        dead_clients = []
        event_name = data.get("event")
        if event_name == "server.telemetry.updated":
            target_server_id = data.get("server_id")
            recipients = list(self._dashboard_websocket_clients)
            if isinstance(target_server_id, int):
                recipients.extend(self._server_websocket_clients.get(target_server_id, []))
        elif event_name == "poll.cycle.completed":
            recipients = list(self._dashboard_websocket_clients)
        else:
            # There is intentionally no catch-all global channel.
            recipients = []

        unique_recipients = []
        recipient_ids = set()
        for ws in recipients:
            if id(ws) not in recipient_ids:
                recipient_ids.add(id(ws))
                unique_recipients.append(ws)

        for ws in unique_recipients:
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.warning(f"WebSocket send failed: {e}")
                dead_clients.append(ws)

        for ws in dead_clients:
            self.remove_websocket_client(ws)

    async def _broadcast_snapshot(self, snapshot: dict[str, Any], cycle_id: int) -> None:
        """Publish a versioned telemetry event rather than an ad-hoc poll row."""
        await self._broadcast({
            "event": "server.telemetry.updated",
            "event_type": "server.telemetry.updated",
            "emitted_at": self._isoformat(self._utcnow()),
            "cycle_id": cycle_id,
            "revision": snapshot["revision"],
            "server_id": snapshot["server_id"],
            "data": snapshot,
        })

    async def _poll_loop(self):
        """Main polling loop — runs while self._running is True."""
        while self._running:
            cycle_started = self._monotonic_clock()
            try:
                await self.poll_all()
            except Exception as e:
                logger.error(f"Poll loop error: {e}")

            # Start-to-start cadence is max(interval, poll duration), based on
            # a monotonic clock so wall-clock changes cannot cause overlap.
            elapsed = max(0.0, self._monotonic_clock() - cycle_started)
            sleep_seconds = max(0.0, settings.sensor_poll_interval - elapsed)
            try:
                await asyncio.sleep(sleep_seconds)
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
        """Re-assert low-speed auto targets on iDRAC7 IPMI paths.

        Some IPMI fan-control paths accept the requested duty cycle but let it
        drift upward again if the manual target is not refreshed periodically.
        When auto control has already decided on a target and cached it in
        ``_last_fan_control_target``, keep sending that same target on a shorter
        cadence than the main sensor poll so cool systems can continue easing
        down toward the configured floor.  After a process restart, seed that
        target from usable PWM observed by the next sensor poll, then use the
        same cadence instead of reapplying it on every poll.
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
            connector = await self._get_connector(server)
            if connector is None:
                continue
            if (
                connector.drac_version != "idrac7"
                and getattr(connector, "_fan_control_backend", None) != "ipmi"
            ):
                continue
            refresh_now = self._monotonic_clock()
            last_refresh = self._last_auto_refresh_at.get(server.id)
            if (
                last_refresh is not None
                and refresh_now - last_refresh < settings.fan_control_idrac7_refresh_interval_seconds
            ):
                continue
            if target is None:
                target = self._last_observed_fan_percent.get(server.id)
                if target is None:
                    continue
            controller = FanController(connector=connector, fan_min=7)
            controller._mode = FanMode.AUTO.value
            target = controller._clamp_fan_percent(target)
            success = await controller._apply_fan_mode("Manual", target)
            if success:
                self._last_fan_control_target[server.id] = target
                self._last_auto_refresh_at[server.id] = refresh_now
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
