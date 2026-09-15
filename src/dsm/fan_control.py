"""Fan control orchestration for Dell servers.

Reads temperatures from iDRAC, derives a target fan percentage using the
controller thresholds, and applies the result through the connector's
version-appropriate control path.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from dsm.idrac_connector import IdracConnector, IdracError, TempSensor
from dsm.models import FanMode

logger = logging.getLogger(__name__)


@dataclass
class FanControlResult:
    """Result of a fan control cycle."""
    target_fan_percent: int
    cpu_temp: float
    disk_temp: Optional[float]
    ambient_temp: float
    action_taken: str  # 'increased', 'decreased', 'unchanged', 'no_action'
    reason: str


class FanController:
    """Fan speed controller for a single server.

    Keeps CPU and disk temps within user-specified ranges by adjusting
    fan speed. The connector's `drac_version` should be treated as the DSM
    control-profile selector used for routing, not necessarily the literal
    user-facing controller generation shown in the UI.

    Live hardware testing established this control split:

    - stored profile `idrac7`: Dell OEM IPMI for reliable manual percentages and
      auto restore, with racadm fallback on systems that reject OEM IPMI fan
      commands for the configured account
    - stored profile `idrac8`: Redfish Dell thermal actions
    - WS-Man: retained only as a legacy fallback when version detection is
      missing or a future older box needs it
    """

    def __init__(
        self,
        connector: IdracConnector,
        cpu_temp_min: float = 45.0,
        cpu_temp_max: float = 70.0,
        disk_temp_min: float = 32.0,
        disk_temp_max: float = 45.0,
        fan_min: int = 7,
        fan_max: int = 100,
    ):
        self.connector = connector
        self.cpu_temp_min = cpu_temp_min
        self.cpu_temp_max = cpu_temp_max
        self.disk_temp_min = disk_temp_min
        self.disk_temp_max = disk_temp_max
        self.fan_min = fan_min
        self.fan_max = fan_max

        # State
        self._current_fan_percent: int = fan_min
        self._mode: str = FanMode.AUTO.value

    def _classify_temp(self, sensor: TempSensor) -> str:
        """Classify sensor by type based on name and physical context."""
        name_lower = sensor.name.lower()
        context = sensor.physical_context.lower()

        if "cpu" in name_lower or context == "cpu":
            return "cpu"
        if "disk" in name_lower or "drive" in name_lower or "hdd" in name_lower or "nvme" in name_lower or "ssd" in name_lower or context == "drive":
            return "disk"
        if "inlet" in name_lower or "exhaust" in name_lower or "ambient" in name_lower or "system" in name_lower or "board" in name_lower:
            return "ambient"
        return "other"

    def _get_temps(self, temperatures: list) -> dict:
        """Extract categorized temps from sensor list."""
        result = {"cpu": [], "disk": [], "ambient": []}

        for sensor in temperatures:
            category = self._classify_temp(sensor)
            if category in result:
                result[category].append(sensor)

        return result

    @staticmethod
    def _normalize_text(value: Optional[str]) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    def _range_value(self, temp_range, field_name: str):
        if temp_range is None:
            return None
        if isinstance(temp_range, dict):
            return temp_range.get(field_name)
        return getattr(temp_range, field_name, None)

    def _thresholds_for_sensor(self, sensor, profile_ranges: Optional[list] = None) -> tuple[float, float]:
        """Resolve min/max thresholds for one temperature sensor."""
        default_min, default_max = self.cpu_temp_min, self.cpu_temp_max
        category = self._classify_temp(sensor)
        if category == "disk":
            default_min, default_max = self.disk_temp_min, self.disk_temp_max
        elif category == "ambient":
            default_min, default_max = self.cpu_temp_min, self.cpu_temp_max

        if not profile_ranges:
            return default_min, default_max

        sensor_name = self._normalize_text(sensor.name)
        sensor_context = self._normalize_text(sensor.physical_context)
        category_matches = []
        generic_matches = []

        for temp_range in profile_ranges:
            component_type = (self._range_value(temp_range, "component_type") or "").lower()
            component_label = self._normalize_text(self._range_value(temp_range, "component_label"))
            if component_type and component_type != category:
                continue

            min_value = self._range_value(temp_range, "temp_min")
            max_value = self._range_value(temp_range, "temp_max")
            if min_value is None and max_value is None:
                continue

            if component_label and (component_label in sensor_name or sensor_name in component_label or component_label in sensor_context):
                category_matches.append((min_value, max_value))
            elif not component_label:
                generic_matches.append((min_value, max_value))

        chosen = category_matches or generic_matches
        if not chosen:
            return default_min, default_max

        mins = [value for value, _ in chosen if value is not None]
        maxes = [value for _, value in chosen if value is not None]
        return (
            min(mins) if mins else default_min,
            min(maxes) if maxes else default_max,
        )

    def _incremental_fan_logic(
        self,
        categorized: dict,
        current_fan_percent: int,
        profile_ranges: Optional[list] = None,
        step_percent: int = 3,
    ) -> tuple[int, str]:
        """Small-step thermal policy: move fans up or down by a few percent.

        Increase when any CPU or drive sensor is above its max threshold.
        Decrease only when every available CPU/drive sensor is below its own
        minimum threshold.  Sensors in the configured band deliberately hold
        the current target: a cool CPU must never reduce airflow for an
        in-range disk (or vice versa).  Ambient telemetry is reported but has
        no configured control band, so it does not independently change duty.
        """
        step_percent = max(1, min(10, step_percent))
        current_fan_percent = max(self.fan_min, min(self.fan_max, current_fan_percent))

        hot_sensors = []
        cool_sensors = []
        monitored_sensors = []

        for category in ("cpu", "disk"):
            for sensor in categorized.get(category, []):
                monitored_sensors.append(sensor)
                min_temp, max_temp = self._thresholds_for_sensor(sensor, profile_ranges)
                temp = sensor.value_celsius
                if temp > max_temp:
                    hot_sensors.append((sensor.name, temp, max_temp))
                elif temp < min_temp:
                    cool_sensors.append((sensor.name, temp, min_temp))

        if hot_sensors:
            target = min(self.fan_max, current_fan_percent + step_percent)
            hottest = max(hot_sensors, key=lambda item: item[1])
            return target, f"{hottest[0]} {hottest[1]:.1f}°C > {hottest[2]:.1f}°C max; increasing fan by {step_percent}%"

        if monitored_sensors and len(cool_sensors) == len(monitored_sensors):
            target = max(self.fan_min, current_fan_percent - step_percent)
            coolest = min(cool_sensors, key=lambda item: item[1])
            return target, f"{coolest[0]} {coolest[1]:.1f}°C < {coolest[2]:.1f}°C min; decreasing fan by {step_percent}%"

        if not monitored_sensors:
            return current_fan_percent, "No CPU or drive temperatures available; holding fan speed"
        return current_fan_percent, "At least one temperature is within its configured range; holding fan speed"

    def _derive_current_fan_percent(self, fans: list) -> Optional[int]:
        """Return only a controller-reported duty, never an RPM estimate."""
        percents = [
            fan.percent for fan in fans
            if getattr(fan, "percent", None) is not None
            and getattr(fan, "percent_source", "pwm") != "rpm_estimate"
        ]
        if percents:
            return int(max(percents))
        return None

    async def _apply_fan_mode(self, mode: str, speed_percent: Optional[int] = None) -> bool:
        """Apply fan mode using the correct iDRAC API for this server."""
        if self.connector.drac_version is None:
            await self.connector.detect_version()

        if self.connector.drac_version == "idrac8":
            return await self.connector.set_fan_mode_redfish(mode, speed_percent)

        if self.connector.drac_version == "idrac7":
            # Prefer the proven immediate IPMI path on iDRAC7-class systems.
            # On some servers that fails fast because the configured account
            # lacks the needed privilege, in which case we fall back to racadm.
            preferred_backend = getattr(self.connector, "_fan_control_backend", None)
            if preferred_backend == "racadm":
                return await self.connector.set_fan_mode_racadm(mode, speed_percent)
            if preferred_backend == "ipmi":
                return await self.connector.set_fan_mode_ipmi(mode, speed_percent)

            success = await self.connector.set_fan_mode_ipmi(mode, speed_percent)
            if success:
                return True

            logger.warning(
                "IPMI iDRAC7 fan control failed for %s; trying racadm fallback",
                self.connector.ip,
            )
            return await self.connector.set_fan_mode_racadm(mode, speed_percent)

        return await self.connector.set_fan_mode_wsman(mode, speed_percent)

    async def control_cycle(
        self,
        sensor_data=None,
        current_fan_percent: Optional[int] = None,
        profile_ranges: Optional[list] = None,
        step_percent: int = 3,
    ) -> FanControlResult:
        """Run one fan control cycle.

        Reads sensors, calculates optimal fan speed, and applies if needed.
        The control mode is intentionally incremental: adjust by only a few
        percentage points per cycle, then let the next poll observe the result.
        """
        if sensor_data is None:
            try:
                sensor_data = await self.connector.get_sensors()
            except IdracError as e:
                logger.error(f"Cannot read sensors for fan control: {e}")
                return FanControlResult(
                    target_fan_percent=self._current_fan_percent,
                    cpu_temp=0,
                    disk_temp=None,
                    ambient_temp=0,
                    action_taken="no_action",
                    reason=f"Sensor read failed: {e}",
                )

        categorized = self._get_temps(sensor_data.temperatures)

        cpu_temps = [s.value_celsius for s in categorized.get("cpu", [])]
        disk_temps = [s.value_celsius for s in categorized.get("disk", [])]
        ambient_temps = [s.value_celsius for s in categorized.get("ambient", [])]

        cpu_temp = max(cpu_temps) if cpu_temps else 0
        disk_temp = max(disk_temps) if disk_temps else None
        ambient_temp = max(ambient_temps) if ambient_temps else 0

        live_fan_percent = current_fan_percent if current_fan_percent is not None else self._derive_current_fan_percent(sensor_data.fans)
        if live_fan_percent is None:
            return FanControlResult(
                target_fan_percent=self._current_fan_percent,
                cpu_temp=cpu_temp,
                disk_temp=disk_temp,
                ambient_temp=ambient_temp,
                action_taken="no_action",
                reason="No controller-reported fan duty or prior DSM target; skipping automatic command",
            )
        self._current_fan_percent = live_fan_percent

        target_fan, reason = self._incremental_fan_logic(
            categorized,
            current_fan_percent=live_fan_percent,
            profile_ranges=profile_ranges,
            step_percent=step_percent,
        )

        if target_fan > live_fan_percent:
            action = "increased"
        elif target_fan < live_fan_percent:
            action = "decreased"
        else:
            action = "unchanged"

        result = FanControlResult(
            target_fan_percent=target_fan,
            cpu_temp=cpu_temp,
            disk_temp=disk_temp,
            ambient_temp=ambient_temp,
            action_taken=action,
            reason=reason,
        )

        should_refresh_manual_override = (
            action == "unchanged"
            and self._mode == FanMode.AUTO.value
            and self.connector.drac_version == "idrac7"
        )

        if (action != "unchanged" or should_refresh_manual_override) and self._mode == FanMode.AUTO.value:
            success = await self._apply_fan_mode("Manual", target_fan)
            if success:
                self._current_fan_percent = target_fan
                if action == "unchanged":
                    logger.info(
                        "Refreshed auto-control manual target at %s%% for %s (CPU: %s°C, Disk: %s°C, Ambient: %s°C)",
                        self._current_fan_percent,
                        self.connector.ip,
                        cpu_temp,
                        disk_temp,
                        ambient_temp,
                    )
                else:
                    logger.info(
                        "Fan speed %s: %s%% (CPU: %s°C, Disk: %s°C, Ambient: %s°C)",
                        action,
                        self._current_fan_percent,
                        cpu_temp,
                        disk_temp,
                        ambient_temp,
                    )
            else:
                logger.warning("Failed to apply fan speed change to %s%%", target_fan)

        return result

    async def set_manual_speed(self, speed: int) -> bool:
        """Set an explicit Dell manual duty percentage (1--100)."""
        self._mode = FanMode.MANUAL.value
        # The automatic-policy floor is not a translation rule for an
        # operator's manual Dell duty command.  Keep the requested byte in the
        # 1--100 range so iDRAC7 OEM IPMI and iDRAC8 Redfish receive the same
        # percentage the operator selected.
        self._current_fan_percent = max(1, min(100, int(speed)))
        return await self._apply_fan_mode("Manual", self._current_fan_percent)

    async def set_auto_mode(self) -> bool:
        """Switch to automatic DSM-managed fan control mode."""
        self._mode = FanMode.AUTO.value
        return await self._apply_fan_mode("Auto")

    async def reset_to_default(self) -> bool:
        """Reset to iDRAC default thermal policy."""
        self._mode = FanMode.PROFILE.value
        return await self._apply_fan_mode("Profile")
