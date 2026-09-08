"""Fan control orchestration for Dell servers.

Reads temperatures from iDRAC, derives a target fan percentage using the
controller thresholds, and applies the result through the connector's
version-appropriate control path.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from dsm.config import settings
from dsm.idrac_connector import IdracConnector, IdracError, TempSensor, FanSensor
from dsm.models import FanMode

logger = logging.getLogger(__name__)


@dataclass
class PIDState:
    """Persistent state for PID controller."""
    integral: float = 0.0
    previous_error: float = 0.0
    last_update: Optional[datetime] = None


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

        # PID parameters
        self.kp = settings.pid_proportional
        self.ki = settings.pid_integral
        self.kd = settings.pid_derivative

        # State
        self._pid = PIDState()
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

    def _calculate_target_temp(self, categorized: dict) -> float:
        """Calculate the effective target temperature for the PID.

        Returns the most critical temperature that needs cooling.
        """
        critical_temps = []

        # CPU temps - target the midpoint of the range
        for sensor in categorized.get("cpu", []):
            if sensor.value_celsius > self.cpu_temp_max:
                # Above max - need to cool down urgently
                critical_temps.append(sensor.value_celsius - self.cpu_temp_max)
            elif sensor.value_celsius > self.cpu_temp_min:
                # In range - no action needed for this sensor
                pass

        # Disk temps
        for sensor in categorized.get("disk", []):
            if sensor.value_celsius > self.disk_temp_max:
                critical_temps.append(sensor.value_celsius - self.disk_temp_max)
            elif sensor.value_celsius < self.disk_temp_min:
                # Below min - fans might be too high
                critical_temps.append(-(self.disk_temp_min - sensor.value_celsius))

        # If nothing critical, return 0 (target met)
        if not critical_temps:
            return 0.0

        # Return the worst deviation
        return max(critical_temps)

    def _pid_step(self, error: float, dt: float) -> int:
        """Single PID control step. Returns fan speed percentage."""
        # Clamp dt to avoid huge jumps
        dt = min(dt, 60.0)

        # Proportional
        p = self.kp * error

        # Integral - with anti-windup
        self._pid.integral += error * dt
        # Anti-windup: clamp integral term
        self._pid.integral = max(-100, min(100, self._pid.integral))
        i = self.ki * self._pid.integral

        # Derivative
        derivative = (error - self._pid.previous_error) / max(dt, 0.1)
        d = self.kd * derivative

        # Combine
        output = p + i + d

        # Convert to fan percentage adjustment
        new_fan = self._current_fan_percent + int(output * 10)

        # Clamp to valid range
        new_fan = max(self.fan_min, min(self.fan_max, new_fan))

        # Update state
        self._pid.previous_error = error
        self._pid.last_update = datetime.now(timezone.utc)

        return new_fan

    def _simple_fan_logic(self, categorized: dict) -> tuple:
        """Simple rule-based fan control fallback.

        Returns (fan_percent, reason).
        """
        cpu_temps = [s.value_celsius for s in categorized.get("cpu", [])]
        disk_temps = [s.value_celsius for s in categorized.get("disk", [])]

        max_cpu = max(cpu_temps) if cpu_temps else 0
        max_disk = max(disk_temps) if disk_temps else 0

        # Start with base speed
        fan_percent = self.fan_min
        reason_parts = []

        # CPU cooling logic - scale from min to max range
        if cpu_temps:
            if max_cpu >= self.cpu_temp_max:
                # Above max - ramp up linearly from 50% to 100%
                overshoot = max_cpu - self.cpu_temp_max
                cpu_fan = min(100, 50 + int(overshoot * 5))
                fan_percent = max(fan_percent, cpu_fan)
                reason_parts.append(f"CPU {max_cpu:.1f}°C > {self.cpu_temp_max}°C max")
            elif max_cpu > self.cpu_temp_min:
                # In range - maintain moderate speed
                fan_percent = max(fan_percent, 30)
                reason_parts.append(f"CPU {max_cpu:.1f}°C in range")
            else:
                # Below min - can reduce
                reason_parts.append(f"CPU {max_cpu:.1f}°C < {self.cpu_temp_min}°C min")

        # Disk cooling logic
        if disk_temps:
            if max_disk >= self.disk_temp_max:
                overshoot = max_disk - self.disk_temp_max
                disk_fan = min(75, 40 + int(overshoot * 10))
                fan_percent = max(fan_percent, disk_fan)
                reason_parts.append(f"Disk {max_disk:.1f}°C > {self.disk_temp_max}°C max")
            elif max_disk < self.disk_temp_min:
                # Disk too cold - fans might be excessive
                reason_parts.append(f"Disk {max_disk:.1f}°C < {self.disk_temp_min}°C min")

        if not reason_parts:
            reason_parts.append("No sensors in range - using minimum speed")

        return fan_percent, "; ".join(reason_parts)

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

        Increase when any monitored sensor is above its max threshold.
        Decrease only when all monitored sensors are below their minimums.
        Otherwise hold steady.
        """
        step_percent = max(1, min(10, step_percent))
        current_fan_percent = max(self.fan_min, min(self.fan_max, current_fan_percent))

        hot_sensors = []
        cool_sensors = []
        in_range_sensors = []

        for category in ("cpu", "disk", "ambient"):
            for sensor in categorized.get(category, []):
                min_temp, max_temp = self._thresholds_for_sensor(sensor, profile_ranges)
                temp = sensor.value_celsius
                if temp > max_temp:
                    hot_sensors.append((sensor.name, temp, max_temp))
                elif temp < min_temp:
                    cool_sensors.append((sensor.name, temp, min_temp))
                else:
                    in_range_sensors.append((sensor.name, temp, min_temp, max_temp))

        if hot_sensors:
            target = min(self.fan_max, current_fan_percent + step_percent)
            hottest = max(hot_sensors, key=lambda item: item[1])
            return target, f"{hottest[0]} {hottest[1]:.1f}°C > {hottest[2]:.1f}°C max; increasing fan by {step_percent}%"

        if cool_sensors:
            target = max(self.fan_min, current_fan_percent - step_percent)
            coolest = min(cool_sensors, key=lambda item: item[1])
            return target, f"{coolest[0]} {coolest[1]:.1f}°C < {coolest[2]:.1f}°C min; decreasing fan by {step_percent}%"

        return current_fan_percent, "Temperatures within configured range; holding fan speed"

    def _derive_current_fan_percent(self, fans: list, fallback: Optional[int] = None) -> int:
        percents = [fan.percent for fan in fans if getattr(fan, "percent", None) is not None]
        if percents:
            return int(max(percents))
        if fallback is not None:
            return fallback
        return self._current_fan_percent

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

        live_fan_percent = current_fan_percent if current_fan_percent is not None else self._derive_current_fan_percent(
            sensor_data.fans,
            self._current_fan_percent,
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
        """Set fan to manual mode with specific speed."""
        self._mode = FanMode.MANUAL.value
        self._current_fan_percent = max(self.fan_min, min(self.fan_max, speed))
        return await self._apply_fan_mode("Manual", self._current_fan_percent)

    async def set_auto_mode(self) -> bool:
        """Switch to automatic DSM-managed fan control mode."""
        self._mode = FanMode.AUTO.value
        self._pid = PIDState()  # Reset PID state
        return await self._apply_fan_mode("Auto")

    async def reset_to_default(self) -> bool:
        """Reset to iDRAC default thermal policy."""
        self._mode = FanMode.PROFILE.value
        self._pid = PIDState()
        return await self._apply_fan_mode("Profile")
