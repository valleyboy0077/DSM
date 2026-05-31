"""PID-based fan control for Dell servers.

Reads temperature sensors from iDRAC, calculates optimal fan speed
using a PID controller, and writes back to iDRAC thermal policy.
"""

import logging
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
    """PID-based fan speed controller for a single server.

    Keeps CPU and disk temps within user-specified ranges by adjusting
    fan speed. Uses iDRAC thermal policy API to set fan mode.
    """

    def __init__(
        self,
        connector: IdracConnector,
        cpu_temp_min: float = 45.0,
        cpu_temp_max: float = 70.0,
        disk_temp_min: float = 32.0,
        disk_temp_max: float = 45.0,
        fan_min: int = 20,
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

    async def control_cycle(self) -> FanControlResult:
        """Run one fan control cycle.

        Reads sensors, calculates optimal fan speed, and applies if needed.
        """
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

        # Use simple rule-based logic (PID is tricky with iDRAC7 limited control)
        target_fan, reason = self._simple_fan_logic(categorized)

        # Determine action
        if target_fan > self._current_fan_percent:
            action = "increased"
        elif target_fan < self._current_fan_percent:
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

        # Apply if changed and in auto mode
        if action != "unchanged" and self._mode == FanMode.AUTO.value:
            success = await self.connector.set_fan_mode_wsman("Manual", target_fan)
            if success:
                self._current_fan_percent = target_fan
                logger.info(
                    f"Fan speed {action}: {self._current_fan_percent}% "
                    f"(CPU: {cpu_temp}°C, Disk: {disk_temp}°C)"
                )
            else:
                logger.warning(f"Failed to apply fan speed change to {target_fan}%")

        return result

    async def set_manual_speed(self, speed: int) -> bool:
        """Set fan to manual mode with specific speed."""
        self._mode = FanMode.MANUAL.value
        self._current_fan_percent = max(self.fan_min, min(self.fan_max, speed))
        return await self.connector.set_fan_mode_wsman("Manual", self._current_fan_percent)

    async def set_auto_mode(self) -> bool:
        """Switch to auto PID control mode."""
        self._mode = FanMode.AUTO.value
        self._pid = PIDState()  # Reset PID state
        return await self.connector.set_fan_mode_wsman("Auto")

    async def reset_to_default(self) -> bool:
        """Reset to iDRAC default thermal policy."""
        self._mode = FanMode.PROFILE.value
        self._pid = PIDState()
        return await self.connector.set_fan_mode_wsman("Profile")
