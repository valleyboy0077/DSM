"""Fan control orchestration for Dell servers.

Reads temperatures from iDRAC, derives a target fan percentage using the
controller thresholds, and applies the result through the connector's
version-appropriate control path.
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Mapping, Optional

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


@dataclass(frozen=True)
class FanControlTuning:
    """Explicit, bounded tuning for DSM's negative-feedback thermal loop."""

    rise_gain_percent_per_c: float = 3.0
    rise_rate_gain_percent_per_c_per_sec: float = 12.0
    fall_gain_percent_per_c: float = 1.0
    rise_activation_margin_c: float = 3.0
    temp_deadband_c: float = 1.0
    rate_deadband_c_per_sec: float = 0.05
    max_rise_step_percent: int = 12
    max_fall_step_percent: int = 3

    def bounded(self, fan_min: int, fan_max: int) -> "FanControlTuning":
        """Return fail-safe tuning values for a controller's fan range.

        Settings validates environment values, but controllers are also used
        directly by the API and tests.  Treat a malformed direct tuning object
        as conservative zero/limited tuning rather than allowing it to create
        a negative correction or an out-of-range target.
        """
        span = max(0, fan_max - fan_min)

        def non_negative(value: object, default: float) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return default
            if not math.isfinite(parsed):
                return default
            return max(0.0, min(100.0, parsed))

        def step(value: object, default: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError, OverflowError):
                return min(span, default)
            return max(0, min(span, parsed))

        return FanControlTuning(
            rise_gain_percent_per_c=non_negative(self.rise_gain_percent_per_c, 0.0),
            rise_rate_gain_percent_per_c_per_sec=non_negative(
                self.rise_rate_gain_percent_per_c_per_sec, 0.0
            ),
            fall_gain_percent_per_c=non_negative(self.fall_gain_percent_per_c, 0.0),
            rise_activation_margin_c=non_negative(self.rise_activation_margin_c, 0.0),
            temp_deadband_c=non_negative(self.temp_deadband_c, 0.0),
            rate_deadband_c_per_sec=non_negative(self.rate_deadband_c_per_sec, 0.0),
            max_rise_step_percent=step(self.max_rise_step_percent, 0),
            max_fall_step_percent=step(self.max_fall_step_percent, 0),
        )


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
        tuning: Optional[FanControlTuning] = None,
    ):
        self.connector = connector
        self.cpu_temp_min = cpu_temp_min
        self.cpu_temp_max = cpu_temp_max
        self.disk_temp_min = disk_temp_min
        self.disk_temp_max = disk_temp_max
        self.fan_min = max(0, min(100, int(fan_min)))
        self.fan_max = max(self.fan_min, min(100, int(fan_max)))
        self.tuning = (tuning or FanControlTuning()).bounded(self.fan_min, self.fan_max)

        # State
        self._current_fan_percent: int = self.fan_min
        self._mode: str = FanMode.AUTO.value

    def _clamp_fan_percent(self, value: int) -> int:
        """Constrain automatic targets to this controller's configured range."""
        return max(self.fan_min, min(self.fan_max, int(value)))

    @staticmethod
    def _classify_temp(sensor: TempSensor) -> str:
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
        step_percent: Optional[int] = 3,
        temperature_rates: Optional[Mapping[str, float]] = None,
    ) -> tuple[int, str]:
        """Return a bounded P+D correction from current temperatures and rates.

        The asymmetric loop deliberately raises faster than it falls.  It only
        cools down once every controlled sensor is safely below its lower band,
        and it holds in the deadband so a stable workload does not hunt.
        Tuning provides the absolute slew limits.  ``step_percent`` is an
        optional tighter per-cycle cap retained for callers that need a more
        conservative response.
        """
        current_fan_percent = self._clamp_fan_percent(current_fan_percent)
        rates = temperature_rates or {}
        heating = []
        cooling = []
        monitored_sensors = []

        for category in ("cpu", "disk"):
            for sensor in categorized.get(category, []):
                monitored_sensors.append(sensor)
                min_temp, max_temp = self._thresholds_for_sensor(sensor, profile_ranges)
                temp = sensor.value_celsius
                rate = rates.get(self.sensor_key(sensor), 0.0)
                # Outside the upper threshold, proportional error dominates.
                # Close to it, only a meaningful positive rate starts a gentle
                # pre-emptive rise; stable in-range temperatures hold.
                if temp > max_temp or (
                    temp >= max_temp - self.tuning.rise_activation_margin_c
                    and rate > self.tuning.rate_deadband_c_per_sec
                ):
                    proportional_error = max(0.0, temp - max_temp)
                    derivative = max(0.0, rate - self.tuning.rate_deadband_c_per_sec)
                    correction = (
                        self.tuning.rise_gain_percent_per_c * proportional_error
                        + self.tuning.rise_rate_gain_percent_per_c_per_sec * derivative
                    )
                    # A temperature already above max must make progress even
                    # if its fractional proportional correction rounds down.
                    heating.append((max(1, round(correction)), sensor.name, temp, max_temp, rate))
                if temp < min_temp - self.tuning.temp_deadband_c:
                    cooling.append((sensor.name, temp, min_temp, rate))

        if heating:
            correction, name, temp, maximum, rate = max(heating, key=lambda item: item[0])
            change = min(self.tuning.max_rise_step_percent, correction)
            if step_percent is not None:
                change = min(change, max(0, int(step_percent)))
            target = self._clamp_fan_percent(current_fan_percent + change)
            return target, (
                f"{name} {temp:.1f}°C (max {maximum:.1f}°C, rate {rate:+.3f}°C/s); "
                f"increasing fan by {target - current_fan_percent}%"
            )

        # Do not reduce airflow if any monitored temperature is in its band or
        # rising: that is the hysteresis which prevents up/down oscillation.
        if monitored_sensors and len(cooling) == len(monitored_sensors) and all(
            rate <= self.tuning.rate_deadband_c_per_sec for _, _, _, rate in cooling
        ):
            name, temp, minimum, rate = min(cooling, key=lambda item: item[1])
            correction = max(1, round(self.tuning.fall_gain_percent_per_c * (minimum - temp)))
            change = min(self.tuning.max_fall_step_percent, correction)
            if step_percent is not None:
                change = min(change, max(0, int(step_percent)))
            target = self._clamp_fan_percent(current_fan_percent - change)
            return target, (
                f"{name} {temp:.1f}°C (min {minimum:.1f}°C, rate {rate:+.3f}°C/s); "
                f"decreasing fan by {current_fan_percent - target}%"
            )

        if not monitored_sensors:
            return current_fan_percent, "No CPU or drive temperatures available; holding fan speed"
        return current_fan_percent, "At least one temperature is within its configured range, deadband, or rising; holding fan speed"

    @staticmethod
    def sensor_key(sensor: TempSensor) -> str:
        """Stable per-server history key for a temperature sensor."""
        return "|".join((sensor.name.strip().lower(), sensor.physical_context.strip().lower()))

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
        step_percent: Optional[int] = 3,
        temperature_rates: Optional[Mapping[str, float]] = None,
        apply: bool = True,
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
                    target_fan_percent=self._clamp_fan_percent(self._current_fan_percent),
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
        live_fan_percent = self._clamp_fan_percent(live_fan_percent)
        self._current_fan_percent = live_fan_percent

        target_fan, reason = self._incremental_fan_logic(
            categorized,
            current_fan_percent=live_fan_percent,
            profile_ranges=profile_ranges,
            step_percent=step_percent,
            temperature_rates=temperature_rates,
        )

        target_fan = self._clamp_fan_percent(target_fan)
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

        if apply and action != "unchanged" and self._mode == FanMode.AUTO.value:
            success = await self._apply_fan_mode("Manual", target_fan)
            if success:
                self._current_fan_percent = target_fan
                logger.info(
                    "Fan speed %s: %s%% (CPU: %s°C, Disk: %s°C, Ambient: %s°C)",
                    action, self._current_fan_percent, cpu_temp, disk_temp, ambient_temp,
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
