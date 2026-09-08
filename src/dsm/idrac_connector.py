"""iDRAC connector — Dell controller access via Redfish, WS-Man, IPMI, and racadm.

Handles authentication, sensor reading, fan control, and power operations.
The internal `drac_version` flag is best understood as a DSM compatibility /
control-routing profile, not always a perfect user-facing hardware-generation
label.
"""

import asyncio
import logging
import shutil
import ssl
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx

try:
    from pyghmi.ipmi import command as pyghmi_command
except Exception:  # pragma: no cover - optional dependency at runtime
    pyghmi_command = None

logger = logging.getLogger(__name__)


class ServerPowerState(str, Enum):
    ON = "On"
    OFF = "Off"
    POWERING_ON = "PoweringOn"
    POWERING_OFF = "PoweringOff"


@dataclass
class TempSensor:
    """Temperature sensor reading."""
    name: str
    value_celsius: float
    physical_context: str  # CPU, SystemBoard, Drive, etc.
    upper_critical: Optional[float] = None
    upper_warning: Optional[float] = None


@dataclass
class FanSensor:
    """Fan speed reading."""
    name: str
    rpm: int
    member_id: str
    percent: Optional[int] = None
    # ``pwm`` is a controller-reported duty cycle.  ``controller_percentage``
    # is another percentage supplied by the controller.  RPM estimates must
    # never be used as an input to the automatic duty controller.
    percent_source: str = "pwm"
    health: str = "OK"


@dataclass
class SystemInfo:
    """System identification info.

    `drac_version` is the internally stored DSM control profile used by backend
    routing. UI code should prefer the server API's `controller_label` when it
    needs a user-facing hardware/controller generation label.
    """
    model: str
    service_tag: str
    bios_version: str
    firmware_version: str  # iDRAC firmware
    drac_version: str  # DSM compatibility/control profile: "idrac7" or "idrac8"
    power_state: str = "Unknown"


@dataclass
class IdracSensorData:
    """Complete sensor snapshot from an iDRAC."""
    system_info: Optional[SystemInfo] = None
    temperatures: list = field(default_factory=list)
    fans: list = field(default_factory=list)


class IdracConnector:
    """Connect to and manage a Dell iDRAC instance.

    Supports Dell controller access across multiple generations/protocols.
    Auto-detects protocol support on first connection, but callers can seed a
    stored DSM control-profile hint when the inventory already knows which
    backend path should be preferred.
    """

    def __init__(self, ip: str, username: str, password: str, drac_version: Optional[str] = None):
        self.ip = ip
        self.username = username
        self.password = password
        self.base_url = f"https://{ip}"
        self._drac_version: Optional[str] = drac_version
        self._firmware_version: Optional[str] = None
        self._fan_control_backend: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._ssl_context = self._make_ssl_context()

    @staticmethod
    def _make_ssl_context() -> ssl.SSLContext:
        """Create permissive SSL context for self-signed iDRAC certs."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                verify=self._ssl_context,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers={"Accept": "application/json"},
                auth=httpx.BasicAuth(self.username, self.password),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def set_control_profile_hint(self, drac_version: Optional[str]) -> None:
        """Seed/update the stored DSM control-profile hint for routing decisions."""
        if drac_version:
            self._drac_version = drac_version

    @staticmethod
    def _odata_path(item) -> str:
        """Normalize Redfish collection members to an @odata path string."""
        if isinstance(item, dict):
            return item.get("@odata.id", "")
        if isinstance(item, str):
            return item
        return ""

    async def _request_many(self, items) -> list[dict]:
        """Fetch multiple Redfish objects concurrently, skipping failures."""
        async def fetch_one(item) -> Optional[dict]:
            path = self._odata_path(item)
            if not path:
                return None
            try:
                return await self._request(path)
            except IdracError:
                return None

        return [
            result
            for result in await asyncio.gather(*(fetch_one(item) for item in items))
            if result is not None
        ]

    async def _request(self, path: str, method: str = "GET", json_body=None) -> dict:
        """Make an authenticated Redfish request."""
        client = await self._get_client()
        url = f"{self.base_url}{path}"

        kwargs = {
            "method": method,
            "url": url,
        }

        if json_body is not None:
            kwargs["json"] = json_body
            kwargs["headers"] = {"Content-Type": "application/json", "Accept": "application/json"}

        try:
            resp = await client.request(**kwargs)
            resp.raise_for_status()
            data = resp.json()
            return data
        except httpx.HTTPStatusError as e:
            error_msg = ""
            try:
                error_data = e.response.json()
                error_msg = error_data.get("error", {}).get("message", str(error_data))
            except Exception:
                error_msg = e.response.text[:200]
            logger.warning(f"iDRAC {self.ip} {e.response.status_code}: {error_msg}")
            raise IdracError(f"HTTP {e.response.status_code}: {error_msg}")
        except httpx.HTTPError as e:
            raise IdracConnectionError(f"Connection to {self.ip} failed: {e}")

    async def _request_wsman(self, xml_body: str) -> str:
        """Make a WS-Man SOAP request. Returns raw XML response."""
        import base64
        client = await self._get_client()
        url = f"{self.base_url}/wsman"
        credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()

        headers = {
            "Content-Type": "application/soap+xml; charset=utf-8",
            "Authorization": f"Basic {credentials}",
            "Accept": "application/soap+xml",
        }

        try:
            resp = await client.post(url, headers=headers, content=xml_body.encode())
            resp.raise_for_status()
            text = resp.text
            return text
        except httpx.HTTPStatusError as e:
            logger.warning(f"WS-Man {self.ip} {e.response.status_code}: {e.response.text[:200]}")
            raise IdracError(f"WS-Man HTTP {e.response.status_code}")
        except httpx.HTTPError as e:
            raise IdracConnectionError(f"WS-Man connection to {self.ip} failed: {e}")

    async def detect_version(self) -> SystemInfo:
        """Detect the DSM control profile and return system info.

        The returned `drac_version` remains the backend compatibility label used
        for control-path selection. It may differ from the exact user-facing
        controller generation shown in the UI, which should come from model-based
        metadata shaping in the servers API.
        """
        # Try Redfish first
        try:
            root = await self._request("/redfish/v1/")
            self._firmware_version = root.get("RedfishVersion", "unknown")

            # Get system details
            system = await self._request("/redfish/v1/Systems/System.Embedded.1/")
            chassis = await self._request("/redfish/v1/Chassis/System.Embedded.1/")

            oem = root.get("Oem", {}).get("Dell", {})
            service_tag = oem.get("ServiceTag", system.get("AssetTag", ""))

            # Detect iDRAC generation more reliably:
            # 1. Try Dell OEM endpoint (works on iDRAC8 with newer firmware)
            # 2. Check firmware version from Manager (iDRAC7 = 2.x, iDRAC8 = 3.x+)
            # 3. Check RedfishVersion (iDRAC7 = 1.0.0/1.0.2, iDRAC8 = 1.1.0+)
            try:
                await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/")
                self._drac_version = "idrac8"
            except IdracError:
                # Check Manager firmware version as fallback
                try:
                    manager = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/")
                    fw = manager.get("FirmwareVersion", "")
                    if fw.startswith("3.") or fw.startswith("4.") or fw.startswith("5.") or fw.startswith("6.") or fw.startswith("7."):
                        self._drac_version = "idrac8"
                    else:
                        self._drac_version = "idrac7"
                except IdracError:
                    # Last resort: check RedfishVersion from root
                    self._drac_version = "idrac8" if self._firmware_version not in ("1.0.0", "1.0.2", "unknown") else "idrac7"

            power_state = system.get("PowerState", "Unknown")

            return SystemInfo(
                model=system.get("Model", "Unknown"),
                service_tag=service_tag,
                bios_version=system.get("BiosVersion", "Unknown"),
                firmware_version=self._firmware_version,
                drac_version=self._drac_version,
                power_state=power_state,
            )
        except IdracConnectionError:
            # Fall back to WS-Man identification
            xml = self._wsman_identify()
            await self._request_wsman(xml)
            # Parse firmware from response
            self._drac_version = "idrac7"
            self._firmware_version = "2.x"  # parsed from response if needed

            return SystemInfo(
                model="Unknown",
                service_tag="",
                bios_version="Unknown",
                firmware_version=self._firmware_version,
                drac_version="idrac7",
                power_state="Unknown",
            )

    async def get_sensors(self) -> IdracSensorData:
        """Get all temperature and fan sensor readings.

        Redfish is the primary source for temperatures, but its older Dell
        implementations frequently omit the fan PWM that DCIM_FanView exposes.
        The automatic controller must use observed controller duty as its
        feedback value, so prefer the same WS-Man-backed inventory used by the
        live telemetry endpoint when it is available.
        """
        data = IdracSensorData()

        try:
            # Get thermal data
            thermal = await self._request("/redfish/v1/Chassis/System.Embedded.1/Thermal/")

            # Parse temperatures
            for temp in thermal.get("Temperatures", []):
                sensor = TempSensor(
                    name=temp.get("Name", "Unknown"),
                    value_celsius=temp.get("ReadingCelsius", 0),
                    physical_context=temp.get("PhysicalContext", "Unknown"),
                    upper_critical=temp.get("UpperThresholdCritical"),
                    upper_warning=temp.get("UpperThresholdNonCritical"),
                )
                data.temperatures.append(sensor)

            # Parse fans
            for fan in thermal.get("Fans", []):
                percent = self._coerce_int(fan.get("Oem", {}).get("Dell", {}).get("PWM"))
                percent_source = "pwm"
                if percent is None:
                    percent = self._coerce_int(fan.get("PercentAvailable"))
                    percent_source = "controller_percentage" if percent is not None else "unavailable"
                fan_sensor = FanSensor(
                    name=fan.get("Name", "Unknown"),
                    rpm=fan.get("Reading", 0),
                    member_id=fan.get("MemberId", ""),
                    percent=percent,
                    percent_source=percent_source,
                    health=fan.get("Status", {}).get("Health", "Unknown"),
                )
                data.fans.append(fan_sensor)

            # Do not let an absent or incomplete Redfish PWM reading make the
            # automatic loop fall back to a prior command.  WS-Man fan
            # inventory is the authoritative available PWM source on the
            # affected iDRAC7 compatibility profile.  Preserve the Redfish
            # readings if the supplementary inventory query is unavailable.
            try:
                inventory = await self.get_fan_inventory()
            except (IdracError, IdracConnectionError) as exc:
                logger.info("Keeping Redfish fan readings for %s: %s", self.ip, exc)
            else:
                inventory_fans = [
                    FanSensor(
                        name=fan["name"],
                        rpm=fan["rpm"],
                        member_id=fan["member_id"],
                        percent=fan["percent"],
                        percent_source=fan["percent_source"],
                        health=fan["health"],
                    )
                    for fan in inventory
                ]
                if inventory_fans:
                    data.fans = inventory_fans

            # Get system info if not already cached
            if not data.system_info:
                data.system_info = await self.detect_version()

        except IdracError as e:
            logger.error(f"Failed to get sensors from {self.ip}: {e}")

        return data

    async def get_fan_inventory(self) -> list[dict]:
        """Get live fan RPM + percentage readings.

        Prefer WS-Man/DCIM_FanView because older iDRAC generations expose PWM
        percentages there more reliably than Redfish. Merge it with Redfish
        by fan identity so a partial WS-Man response cannot hide otherwise
        valid Redfish readings.
        """
        wsman_fans: list[dict] = []
        try:
            fan_rows = await self._wsman_enumerate("DCIM_FanView")
            for row in fan_rows:
                percent = self._coerce_int(row.get("PWM"))
                wsman_fans.append({
                    "name": row.get("DeviceDescription") or row.get("FQDD") or row.get("InstanceID") or "Fan",
                    "member_id": row.get("FQDD") or row.get("InstanceID") or "",
                    "rpm": self._coerce_int(row.get("CurrentReading")) or 0,
                    "percent": percent,
                    "percent_source": "pwm" if percent is not None else "unavailable",
                    "health": self._status_label(row.get("PrimaryStatus")),
                    "source": "wsman",
                })
            wsman_fans = [fan for fan in wsman_fans if fan["rpm"] > 0 or fan["percent"] is not None]
        except (IdracError, IdracConnectionError, ET.ParseError, TypeError, AttributeError) as exc:
            # A malformed/partial SOAP payload is inventory-local.  Redfish
            # thermal telemetry is still useful and must remain available.
            logger.info(f"Falling back to Redfish fan telemetry for {self.ip}: {exc}")

        thermal = await self._request("/redfish/v1/Chassis/System.Embedded.1/Thermal/")
        redfish_fans = []
        for fan in thermal.get("Fans", []):
            reading = self._coerce_int(fan.get("Reading")) or 0
            percent = self._coerce_int(fan.get("Oem", {}).get("Dell", {}).get("PWM"))
            percent_source = "pwm"
            if percent is None:
                percent = self._coerce_int(fan.get("PercentAvailable"))
                percent_source = "controller_percentage" if percent is not None else "unavailable"
            if percent is None:
                upper = self._coerce_int(fan.get("UpperThresholdCritical")) or self._coerce_int(fan.get("MaxReadingRange"))
                if upper and upper > 0 and reading > 0:
                    percent = max(1, min(100, round((reading / upper) * 100)))
                    percent_source = "rpm_estimate"
            redfish_fans.append({
                "name": fan.get("FanName") or fan.get("Name") or "Fan",
                "member_id": fan.get("MemberId") or fan.get("Id") or "",
                "rpm": reading,
                "percent": percent,
                "percent_source": percent_source,
                "health": fan.get("Status", {}).get("Health", "Unknown"),
                "source": "redfish",
            })
        return self._merge_fan_inventory(redfish_fans, wsman_fans)

    @staticmethod
    def _fan_inventory_identity(fan: dict) -> str:
        """Return a stable fan identity across Redfish and DCIM inventory."""
        value = fan.get("member_id") or fan.get("name") or ""
        normalized = "".join(character for character in str(value).lower() if character.isalnum())
        # DCIM often calls a fan ``Fan.Embedded.1`` while Redfish uses
        # ``Fan1``.  Removing this transport-specific label aligns them.
        return normalized.replace("embedded", "")

    @classmethod
    def _merge_fan_inventory(cls, redfish_fans: list[dict], wsman_fans: list[dict]) -> list[dict]:
        """Overlay usable WS-Man values without dropping Redfish-only fans."""
        merged = [dict(fan) for fan in redfish_fans]
        redfish_indexes = {
            cls._fan_inventory_identity(fan): index
            for index, fan in enumerate(merged)
            if cls._fan_inventory_identity(fan)
        }

        for wsman_fan in wsman_fans:
            index = redfish_indexes.get(cls._fan_inventory_identity(wsman_fan))
            if index is None:
                merged.append(dict(wsman_fan))
                continue

            fan = merged[index]
            if wsman_fan["rpm"] > 0:
                fan["rpm"] = wsman_fan["rpm"]
            if wsman_fan["percent"] is not None:
                fan["percent"] = wsman_fan["percent"]
                fan["percent_source"] = wsman_fan["percent_source"]
                fan["source"] = "wsman"
            if wsman_fan["health"] not in {"", "Unknown"}:
                fan["health"] = wsman_fan["health"]

        return merged

    async def get_power_state(self) -> str:
        """Get current power state of the server."""
        try:
            system = await self._request("/redfish/v1/Systems/System.Embedded.1/")
            return system.get("PowerState", "Unknown")
        except IdracError:
            return "Unknown"

    async def set_power_state(self, state: str) -> bool:
        """Set power state: On, ForceOff, ForceRestart, GracefulShutdown, PushPowerButton."""
        valid_states = ["On", "ForceOff", "ForceRestart", "GracefulShutdown", "PushPowerButton"]
        if state not in valid_states:
            raise ValueError(f"Invalid power state: {state}. Must be one of {valid_states}")

        try:
            await self._request(
                "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
                method="POST",
                json_body={"ResetType": state},
            )
            return True
        except IdracError as e:
            logger.error(f"Failed to set power state to {state}: {e}")
            return False

    async def set_fan_mode_wsman(self, mode: str, speed_percent: Optional[int] = None) -> bool:
        """Set fan control mode via WS-Man SOAP (iDRAC7).

        Args:
            mode: 'Auto', 'Manual', or 'Profile'
            speed_percent: Fan speed 1-100 (only for Manual mode)
        """
        if self._drac_version != "idrac7":
            logger.warning("WS-Man fan control is for iDRAC7. Use set_fan_mode_redfish for iDRAC8.")

        try:
            if mode == "Manual" and speed_percent is not None:
                # iDRAC7 needs both steps: switch thermal policy to Manual,
                # then set the desired fan speed.
                await self._request_wsman(self._wsman_set_fan_mode("Manual"))
                await self._request_wsman(self._wsman_set_fan_speed(speed_percent))
            else:
                await self._request_wsman(self._wsman_set_fan_mode(mode))
            return True
        except IdracError as e:
            logger.error(f"Failed to set fan mode via WS-Man: {e}")
            return False

    async def set_fan_mode_ipmi(self, mode: str, speed_percent: Optional[int] = None) -> bool:
        """Set fan mode via Dell OEM IPMI raw commands.

        Works well on older Dell iDRAC generations where WS-Man/Redfish
        fan control actions are inconsistent.

        Manual mode sequence:
        - raw 0x30 0x30 0x01 0x00  -> enable manual mode
        - raw 0x30 0x30 0x02 0xff <pct> -> set fan speed percent

        Auto/Profile sequence:
        - raw 0x30 0x30 0x01 0x01  -> return control to iDRAC automatic mode
        """
        if pyghmi_command is None:
            logger.error("pyghmi is not installed; cannot use IPMI fan control")
            return False

        logger.info(f"Using IPMI fan control on {self.ip}: mode={mode} speed={speed_percent}")

        script = r'''
import sys
from pyghmi.ipmi import command as pyghmi_command
ip, username, password, mode, speed = sys.argv[1:6]
ipmi = pyghmi_command.Command(bmc=ip, userid=username, password=password, privlevel=4)
try:
    if mode == "Manual":
        pct = max(1, min(100, int(speed)))
        resp1 = ipmi.raw_command(netfn=0x30, command=0x30, data=[0x01, 0x00])
        if resp1.get("code", 0) != 0:
            raise SystemExit(f"IPMI manual mode failed: {resp1}")
        resp2 = ipmi.raw_command(netfn=0x30, command=0x30, data=[0x02, 0xff, pct])
        if resp2.get("code", 0) != 0:
            raise SystemExit(f"IPMI set speed failed: {resp2}")
        print(f"manual:{pct}")
    else:
        resp = ipmi.raw_command(netfn=0x30, command=0x30, data=[0x01, 0x01])
        if resp.get("code", 0) != 0:
            raise SystemExit(f"IPMI auto mode failed: {resp}")
        print("auto")
finally:
    try:
        ipmi.ipmi_session.logout()
    except Exception:
        pass
'''

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                self.ip,
                self.username,
                self.password,
                mode,
                str(speed_percent or 0),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise IdracError((stderr or stdout).decode().strip() or f"IPMI subprocess failed with code {proc.returncode}")
            result = stdout.decode().strip()
            if mode == "Manual" and speed_percent is not None:
                logger.info(f"IPMI manual fan speed applied on {self.ip}: {speed_percent}% ({result})")
            else:
                logger.info(f"IPMI automatic fan control restored on {self.ip} ({result})")
            self._fan_control_backend = "ipmi"
            return True
        except Exception as e:
            logger.error(f"Failed to set fan mode via IPMI: {e}")
            return False

    async def _run_racadm(self, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """Run local racadm against the remote iDRAC."""
        racadm = shutil.which("racadm")
        if not racadm:
            raise IdracError("racadm is not installed")

        cmd = [racadm, "-r", self.ip, "-u", self.username, "-p", self.password, *args]
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise IdracError(f"racadm {' '.join(args)} failed: {detail}")
        return proc

    async def get_thermal_settings_racadm(self) -> dict[str, str]:
        """Read current Dell thermal settings via racadm."""
        proc = await self._run_racadm(["get", "system.thermalsettings"])
        settings: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Security Alert:") or line.startswith("Continuing execution"):
                continue
            if line.startswith("[") or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                settings[key.strip()] = value.strip()
        return settings

    async def set_fan_mode_racadm(self, mode: str, speed_percent: Optional[int] = None) -> bool:
        """Set fan control via racadm thermal settings.

        This is an iDRAC7 fallback for systems where Dell OEM IPMI fan control
        is unavailable to the configured account.  Its ``MinimumFanSpeed``
        setting is a minimum floor, not an exact manual PWM duty; iDRAC may
        operate the fans above the requested value.
        """
        try:
            if mode == "Manual" and speed_percent is not None:
                clamped = max(1, min(100, int(speed_percent)))
                await self._run_racadm(
                    ["set", "system.thermalsettings.MinimumFanSpeed", str(clamped)],
                    timeout=120,
                )
                logger.info(f"racadm minimum fan-speed floor applied on {self.ip}: {clamped}%")
            else:
                # On boxes where racadm MinimumFanSpeed overrides are supported,
                # 255 is the automatic/default sentinel.
                await self._run_racadm(
                    ["set", "system.thermalsettings.MinimumFanSpeed", "255"],
                    timeout=120,
                )
                logger.info(f"racadm automatic fan control restored on {self.ip}")

            self._fan_control_backend = "racadm"
            return True
        except IdracError as e:
            logger.error(f"Failed to set fan mode via racadm: {e}")
            return False

    async def set_fan_mode_redfish(self, mode: str, speed_percent: Optional[int] = None) -> bool:
        """Set fan control mode via Redfish (iDRAC8+).

        Uses Dell OEM ThermalService to set thermal policy and fan speed.

        Args:
            mode: 'Auto', 'Manual', or 'Profile'
            speed_percent: Fan speed 1-100 (only for Manual mode)
        """
        if self._drac_version != "idrac8":
            logger.warning("Redfish fan control is for iDRAC8+. Use set_fan_mode_wsman for iDRAC7.")

        try:
            # Get the ThermalService path
            thermal = await self._request("/redfish/v1/Chassis/System.Embedded.1/Thermal/")
            thermal_service_path = thermal.get("@odata.id", "")

            if not thermal_service_path:
                logger.error("No ThermalService path found in Redfish response")
                return False

            # Get the ThermalService details
            thermal_service = await self._request(thermal_service_path)

            # Get Dell OEM ThermalService
            dell_thermal = thermal_service.get("Oem", {}).get("Dell", {}).get("ThermalService", {})
            dell_thermal_path = dell_thermal.get("@odata.id", "")

            if not dell_thermal_path:
                logger.error("No Dell ThermalService path found in Redfish response")
                return False

            if mode == "Manual" and speed_percent is not None:
                await self._request(
                    f"{dell_thermal_path}/Actions/DellThermalService.SetThermalPolicy",
                    method="POST",
                    json_body={"ThermalPolicy": "Manual"},
                )
                # Set fan speed via Dell ThermalService SetFanSpeed
                clamped = max(1, min(100, speed_percent))
                await self._request(
                    f"{dell_thermal_path}/Actions/DellThermalService.SetFanSpeed",
                    method="POST",
                    json_body={"FanSpeedPercent": clamped},
                )
            else:
                # Set thermal policy via Dell ThermalService SetThermalPolicy
                await self._request(
                    f"{dell_thermal_path}/Actions/DellThermalService.SetThermalPolicy",
                    method="POST",
                    json_body={"ThermalPolicy": mode},
                )

            return True
        except IdracError as e:
            logger.error(f"Failed to set fan mode via Redfish: {e}")
            return False

    def _wsman_identify(self) -> str:
        """WS-Man Identify request."""
        return '''<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd">
  <env:Header>
    <wsmid:ResourceURI scheme="http"
      namespace="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2"
      service="DCIM_ComputerSystemView"
      classname="DCIM_ComputerSystemView"/>
  </env:Header>
  <env:Body>
    <wsmid:Identify/>
  </env:Body>
</env:Envelope>'''

    def _wsman_set_fan_mode(self, mode: str) -> str:
        """WS-Man envelope for setting fan thermal policy."""
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:wsman="http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd"
 xmlns:n1="http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_ThermalService">
  <env:Header>
    <wsa:To>/wsman</wsa:To>
    <wsman:ResourceURI>http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_ThermalService</wsman:ResourceURI>
    <wsa:Action>http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_ThermalService/DCIM_ThermalService_SetThermalPolicy</wsa:Action>
    <wsa:MessageID>uuid:00000000-0000-0000-0000-000000000002</wsa:MessageID>
    <wsa:ReplyTo><wsa:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:Address></wsa:ReplyTo>
    <wsman:SelectorSet>
      <wsman:Selector Name="InstanceID">Dell Thermal Service</wsman:Selector>
    </wsman:SelectorSet>
  </env:Header>
  <env:Body>
    <n1:DCIM_ThermalService_SetThermalPolicy_INPUT>
      <n1:ThermalPolicy>{mode}</n1:ThermalPolicy>
    </n1:DCIM_ThermalService_SetThermalPolicy_INPUT>
  </env:Body>
</env:Envelope>'''

    def _wsman_set_fan_speed(self, speed_percent: int) -> str:
        """WS-Man envelope for setting manual fan speed."""
        clamped = max(1, min(100, speed_percent))
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:wsman="http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd"
 xmlns:n1="http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_CoolingUnitView">
  <env:Header>
    <wsa:To>/wsman</wsa:To>
    <wsman:ResourceURI>http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_CoolingUnitView</wsman:ResourceURI>
    <wsa:Action>http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_CoolingUnitView/DCIM_CoolingUnitView_SetDesiredSpeed</wsa:Action>
    <wsa:MessageID>uuid:00000000-0000-0000-0000-000000000003</wsa:MessageID>
    <wsa:ReplyTo><wsa:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:Address></wsa:ReplyTo>
    <wsman:SelectorSet>
      <wsman:Selector Name="InstanceID">Dell Cooling Unit</wsman:Selector>
    </wsman:SelectorSet>
  </env:Header>
  <env:Body>
    <n1:DCIM_CoolingUnitView_SetDesiredSpeed_INPUT>
      <n1:DesiredSpeed>{clamped}</n1:DesiredSpeed>
    </n1:DCIM_CoolingUnitView_SetDesiredSpeed_INPUT>
  </env:Body>
</env:Envelope>'''

    def _wsman_get_temps(self) -> str:
        """WS-Man envelope for getting temperature sensors."""
        return '''<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd">
  <env:Header>
    <wsmid:ResourceURI xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd">
      http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_TemperatureView:DCIM_TemperatureView
    </wsmid:ResourceURI>
  </env:Header>
  <env:Body>
    <wsmid:Enumerate xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd"/>
  </env:Body>
</env:Envelope>'''

    @staticmethod
    def _wsman_envelope(resource_uri: str) -> str:
        """Optimized WS-Man Enumerate envelope for Dell DCIM classes."""
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:wsman="http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd"
 xmlns:wsen="http://schemas.xmlsoap.org/ws/2004/09/enumeration">
  <env:Header>
    <wsa:To>/wsman</wsa:To>
    <wsman:ResourceURI>{resource_uri}</wsman:ResourceURI>
    <wsa:Action>http://schemas.xmlsoap.org/ws/2004/09/enumeration/Enumerate</wsa:Action>
    <wsa:MessageID>uuid:00000000-0000-0000-0000-000000000001</wsa:MessageID>
    <wsa:ReplyTo><wsa:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:Address></wsa:ReplyTo>
  </env:Header>
  <env:Body>
    <wsen:Enumerate>
      <wsman:OptimizeEnumeration/>
      <wsman:MaxElements>128</wsman:MaxElements>
    </wsen:Enumerate>
  </env:Body>
</env:Envelope>'''


    @staticmethod
    def _xml_local_name(tag: str) -> str:
        return tag.split('}', 1)[1] if '}' in tag else tag

    @staticmethod
    def _status_label(value) -> str:
        mapping = {
            '0': 'Unknown',
            '1': 'OK',
            '2': 'Degraded',
            '3': 'Critical',
            '4': 'NonRecoverable',
        }
        return mapping.get(str(value), str(value) if value is not None else 'Unknown')

    @staticmethod
    def _power_state_label(value) -> str:
        mapping = {
            '1': 'Other',
            '2': 'On',
            '3': 'Off',
            '4': 'PowerCycle',
            '5': 'PoweringOn',
            '6': 'PoweringOff',
        }
        return mapping.get(str(value), str(value) if value is not None else 'Unknown')

    @staticmethod
    def _coerce_int(value) -> Optional[int]:
        try:
            if value is None or value == "":
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None

    async def _wsman_enumerate(self, class_name: str) -> list[dict]:
        """Enumerate a Dell DCIM WS-Man class and return instance dicts."""
        resource_uri = f"http://schemas.dell.com/wbem/wscim/1/cim-schema/2/{class_name}"
        response = await self._request_wsman(self._wsman_envelope(resource_uri))
        root = ET.fromstring(response)
        items = root.findall('.//{http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd}Items/*')
        parsed: list[dict] = []
        for item in items:
            row: dict = {}
            for child in item:
                key = self._xml_local_name(child.tag)
                value = child.text
                if key in row:
                    existing = row[key]
                    if isinstance(existing, list):
                        existing.append(value)
                    else:
                        row[key] = [existing, value]
                else:
                    row[key] = value
            if row:
                parsed.append(row)
        return parsed


    # ─── User Management ──────────────────────────────────────────────

    async def list_idrac_users(self) -> list[dict]:
        """List iDRAC user account slots.

        Older iDRAC7 systems have proven more reliable when account slots are
        probed directly in numeric order instead of walking the collection or
        fanning out concurrent detail requests.
        """
        users: list[dict] = []
        for account_id in range(1, 17):
            try:
                account_data = await self._request(f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{account_id}")
                username = (account_data.get("UserName") or "").strip()
                if not username:
                    continue
                users.append({
                    "id": account_data.get("Id"),
                    "username": username,
                    "enabled": account_data.get("Enabled", False),
                    "role": account_data.get("RoleId", ""),
                    "role_name": account_data.get("RoleName", ""),
                    "privileges": account_data.get("Privileges", []),
                    "lan_privilege": account_data.get("MaximumLANUserPrivilegeGranted", ""),
                    "serial_privilege": account_data.get("MaximumSerialPortUserPrivilegeGranted", ""),
                    "access": account_data.get("Access", ""),
                })
            except IdracError:
                continue
        return users

    async def create_user(self, username: str, password: str, role: str = "ReadOnly") -> str:
        """Create or update a user on iDRAC.

        Newer iDRAC Redfish implementations accept numeric RoleId plus privilege
        fields. Older iDRAC7 implementations reject those fields and only accept
        a small PATCH body with a string RoleId (for example ``Operator``).

        This method now tries the richer payload first, then falls back to an
        iDRAC7-compatible payload before failing.
        """
        # Map role name to Redfish RoleId, Privileges, and IPMI privilege levels
        role_map = {
            "Administrator": {
                "role_id": "500",
                "role_id_string": "Administrator",
                "privileges": ["Login", "ConfigureUsers", "ConfigureComponents", "ConfigureSelf", "RemoteConsole", "VirtualMedia", "Debug"],
                "lan_privilege": "Administrator",
                "serial_privilege": "Administrator",
                "access": "Administrator",
            },
            "Operator": {
                "role_id": "501",
                "role_id_string": "Operator",
                "privileges": ["Login", "ConfigureComponents", "ConfigureSelf", "RemoteConsole", "VirtualMedia"],
                "lan_privilege": "Operator",
                "serial_privilege": "Operator",
                "access": "Operator",
            },
            "ReadOnly": {
                "role_id": "502",
                "role_id_string": "ReadOnly",
                "privileges": ["Login", "RemoteConsole", "VirtualMedia"],
                "lan_privilege": "User",
                "serial_privilege": "User",
                "access": "User",
            },
        }
        role_config = role_map.get(role, role_map["ReadOnly"])

        def payloads(include_username: bool) -> list[dict]:
            modern_payload = {
                "Password": password,
                "Enabled": True,
                "RoleId": role_config["role_id"],
                "Privileges": role_config["privileges"],
                "MaximumLANUserPrivilegeGranted": role_config["lan_privilege"],
                "MaximumSerialPortUserPrivilegeGranted": role_config["serial_privilege"],
                "Access": role_config["access"],
            }
            legacy_payload = {
                "Password": password,
                "Enabled": True,
                "RoleId": role_config["role_id_string"],
            }
            if include_username:
                modern_payload = {"UserName": username, **modern_payload}
                legacy_payload = {"UserName": username, **legacy_payload}
            return [modern_payload, legacy_payload]

        async def patch_account(account_id: str | int, include_username: bool) -> None:
            account_path = f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{account_id}"
            last_error: Optional[IdracError] = None
            for body in payloads(include_username):
                try:
                    await self._request(account_path, method="PATCH", json_body=body)
                    return
                except IdracError as e:
                    last_error = e
                    logger.info(
                        "Account PATCH failed on %s for %s with body keys %s: %s",
                        self.ip,
                        username,
                        sorted(body.keys()),
                        e,
                    )
            if last_error is not None:
                raise last_error
            raise IdracError("Unknown account patch failure")

        first_empty_slot: Optional[int] = None
        for slot_id in range(2, 17):
            try:
                account = await self._request(f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{slot_id}")
            except IdracError:
                continue

            slot_username = (account.get("UserName") or "").strip()
            if slot_username == username:
                try:
                    await patch_account(slot_id, include_username=False)
                    return "success"
                except IdracError as e:
                    logger.error(f"Failed to update user {username} on {self.ip}: {e}")
                    raise

            if first_empty_slot is None and not slot_username:
                first_empty_slot = slot_id

        if first_empty_slot is not None:
            try:
                await patch_account(first_empty_slot, include_username=True)
                return "success"
            except IdracError as e:
                logger.warning(f"Failed to create user {username} in slot {first_empty_slot} on {self.ip}: {e}")
                raise

        raise IdracError("No compatible empty iDRAC account slot was accepted")

    async def delete_user(self, username: str) -> bool:
        """Delete a user from iDRAC."""
        try:
            users = await self.list_idrac_users()
            for u in users:
                if u["username"] == username:
                    account_id = u["id"]
                    # Disable and clear the account
                    await self._request(
                        f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{account_id}",
                        method="PATCH",
                        json_body={"Enabled": False},
                    )
                    await self._request(
                        f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{account_id}",
                        method="DELETE",
                    )
                    return True
            return True  # User not found = already clean

        except IdracError as e:
            logger.error(f"Failed to delete user {username} on {self.ip}: {e}")
            return False

    # ─── iDRAC Settings Scraping ──────────────────────────────────────

    async def get_network_settings(self) -> dict:
        """Get iDRAC network configuration."""
        try:
            data = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/NetworkProtocol/")
            nic = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces/NIC.1/")
            return {
                "network_protocol": data,
                "ethernet": nic,
            }
        except IdracError as e:
            logger.error(f"Failed to get network settings from {self.ip}: {e}")
            return {}

    async def get_sel(self, clear: bool = False) -> dict:
        """Get System Event Log. Optionally clear."""
        try:
            data = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/")
            entries = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries/")
            if clear:
                await self._request(
                    "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Actions/LogService.ClearLog",
                    method="POST",
                    json_body={},
                )
            return {"service": data, "entries": entries.get("Members", [])}
        except IdracError as e:
            logger.error(f"Failed to get SEL from {self.ip}: {e}")
            return {}

    async def get_hardware_inventory(self) -> dict:
        """Get hardware inventory summary for UI display.

        Prefer WS-Man on iDRAC7 because the Dell DCIM classes expose richer,
        flatter inventory data than the limited Redfish implementation.
        """
        try:
            if self._drac_version is None:
                await self.detect_version()
        except IdracError:
            # If version detection is flaky, keep going and try the existing
            # Redfish path below.
            pass

        if self._drac_version == "idrac7":
            try:
                system_rows = await self._wsman_enumerate("DCIM_SystemView")
                cpu_rows = await self._wsman_enumerate("DCIM_CPUView")
                memory_rows = await self._wsman_enumerate("DCIM_MemoryView")
                controller_rows = await self._wsman_enumerate("DCIM_ControllerView")
                disk_rows = await self._wsman_enumerate("DCIM_PhysicalDiskView")
                nic_rows = await self._wsman_enumerate("DCIM_NICView")
                psu_rows = await self._wsman_enumerate("DCIM_PowerSupplyView")

                system = system_rows[0] if system_rows else {}
                populated_memory = [row for row in memory_rows if str(row.get("Size") or "0") not in ("0", "None", "")]

                return {
                    "source": "wsman",
                    "protocol": "WS-Man",
                    "model": system.get("Model") or system.get("SystemGeneration") or "Unknown",
                    "service_tag": system.get("ServiceTag") or system.get("ChassisServiceTag") or "",
                    "bios_version": system.get("BIOSVersionString") or "Unknown",
                    "idrac_firmware": system.get("LifecycleControllerVersion") or self._firmware_version or "Unknown",
                    "power_state": self._power_state_label(system.get("PowerState")),
                    "system_health": self._status_label(system.get("RollupStatus") or system.get("CurrentRollupStatus") or system.get("PrimaryStatus")),
                    "cpu_health": self._status_label(system.get("CPURollupStatus")),
                    "memory_health": self._status_label(system.get("MemoryRollupStatus") or system.get("SysMemPrimaryStatus")),
                    "storage_health": self._status_label(system.get("StorageRollupStatus")),
                    "fan_health": self._status_label(system.get("FanRollupStatus")),
                    "power_health": self._status_label(system.get("PSRollupStatus")),
                    "cpu_count": len(cpu_rows),
                    "memory_module_count": len(populated_memory),
                    "memory_total_mb": system.get("SysMemTotalSize") or "0",
                    "storage_controller_count": len(controller_rows),
                    "disk_count": len(disk_rows),
                    "nic_count": len(nic_rows),
                    "power_supply_count": len(psu_rows),
                    "system": system,
                    "processors": cpu_rows,
                    "memory_modules": populated_memory,
                    "storage_controllers": controller_rows,
                    "disks": disk_rows,
                    "ethernet_interfaces": nic_rows,
                    "power_supplies": psu_rows,
                }
            except IdracError as e:
                logger.warning("WS-Man hardware inventory failed on %s, falling back to Redfish: %s", self.ip, e)

        result = {}
        try:
            system = await self._request("/redfish/v1/Systems/System.Embedded.1/")
            chassis = await self._request("/redfish/v1/Chassis/System.Embedded.1/")
            memory = await self._request("/redfish/v1/Systems/System.Embedded.1/Memory/")
            storage = await self._request("/redfish/v1/Systems/System.Embedded.1/Storage/")
            nic_collection = await self._request("/redfish/v1/Systems/System.Embedded.1/EthernetInterfaces/")

            memory_members = memory.get("Members", [])
            storage_members = storage.get("Members", [])
            nic_members = nic_collection.get("Members", [])

            result = {
                "source": "redfish",
                "protocol": "Redfish",
                "model": system.get("Model"),
                "service_tag": system.get("AssetTag") or chassis.get("SerialNumber") or "",
                "bios_version": system.get("BiosVersion"),
                "idrac_firmware": self._firmware_version or "Unknown",
                "power_state": system.get("PowerState"),
                "system_health": (system.get("Status") or {}).get("Health", "Unknown"),
                "cpu_count": ((system.get("ProcessorSummary") or {}).get("Count") or len(system.get("Processors", []))),
                "memory_module_count": len(memory_members),
                "memory_total_gib": ((system.get("MemorySummary") or {}).get("TotalSystemMemoryGiB")),
                "storage_controller_count": len(storage_members),
                "nic_count": len(nic_members),
                "system": {
                    "Id": system.get("Id"),
                    "Name": system.get("Name"),
                    "Manufacturer": system.get("Manufacturer"),
                    "Model": system.get("Model"),
                    "PartNumber": system.get("PartNumber"),
                    "SerialNumber": system.get("SerialNumber"),
                    "PowerState": system.get("PowerState"),
                    "BiosVersion": system.get("BiosVersion"),
                    "ProcessorSummary": system.get("ProcessorSummary"),
                    "MemorySummary": system.get("MemorySummary"),
                    "Status": system.get("Status"),
                },
                "chassis": {
                    "Id": chassis.get("Id"),
                    "Name": chassis.get("Name"),
                    "Manufacturer": chassis.get("Manufacturer"),
                    "Model": chassis.get("Model"),
                    "SerialNumber": chassis.get("SerialNumber"),
                    "ChassisType": chassis.get("ChassisType"),
                    "Status": chassis.get("Status"),
                },
                "processors": system.get("Processors", []),
                "memory_modules": memory_members,
                "storage_controllers": storage_members,
                "ethernet_interfaces": nic_members,
                "power_supplies": chassis.get("PowerControls", []),
            }

        except IdracError as e:
            logger.error(f"Failed to get hardware inventory from {self.ip}: {e}")

        return result


    async def get_power_settings(self) -> dict:
        """Get power management settings."""
        result = {}
        try:
            system = await self._request("/redfish/v1/Systems/System.Embedded.1/")
            result["power_state"] = system.get("PowerState", "Unknown")

            # Power metrics
            chassis = await self._request("/redfish/v1/Chassis/System.Embedded.1/")
            result["power_metrics"] = chassis.get("Power", {})

            # Dell OEM power settings
            try:
                result["dell_power"] = await self._request(
                    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellPowerService/DellPowerManager/"
                )
            except IdracError:
                result["dell_power"] = {}

        except IdracError as e:
            logger.error(f"Failed to get power settings from {self.ip}: {e}")

        return result

    async def get_storage_settings(self) -> dict:
        """Get storage configuration summary quickly for UI display."""
        result = {}
        try:
            storage = await self._request("/redfish/v1/Systems/System.Embedded.1/Storage/")
            controllers = []
            for detail in await self._request_many(storage.get("Members", [])):
                controllers.append({
                    "Id": detail.get("Id"),
                    "Name": detail.get("Name"),
                    "Description": detail.get("Description"),
                    "Status": detail.get("Status"),
                    "Drives": detail.get("Drives", []),
                    "Volumes": detail.get("Volumes", []),
                    "Links": detail.get("Links", {}),
                })
            result["controllers"] = controllers
            result["controller_count"] = len(controllers)
        except IdracError as e:
            logger.error(f"Failed to get storage settings from {self.ip}: {e}")
        return result

    async def get_bios_settings(self) -> dict:
        """Get BIOS configuration."""
        try:
            settings = await self._request("/redfish/v1/Systems/System.Embedded.1/Bios/Settings/")
            attributes = await self._request("/redfish/v1/Systems/System.Embedded.1/Bios/Attributes/")
            return {
                "settings": settings,
                "attributes": attributes.get("Attributes", {}),
            }
        except IdracError as e:
            logger.error(f"Failed to get BIOS settings from {self.ip}: {e}")
            return {}

    async def get_security_settings(self) -> dict:
        """Get iDRAC security settings."""
        result = {}
        try:
            managers = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/")
            result["manager"] = managers

            # Security service
            try:
                result["security"] = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/Security/")
            except IdracError:
                pass

            # Network protocol security
            try:
                result["network_protocol"] = await self._request(
                    "/redfish/v1/Managers/iDRAC.Embedded.1/NetworkProtocol/"
                )
            except IdracError:
                pass

        except IdracError as e:
            logger.error(f"Failed to get security settings from {self.ip}: {e}")

        return result

    async def get_firmware_info(self) -> dict:
        """Get firmware/update information."""
        result = {}
        try:
            # iDRAC firmware
            manager = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/")
            result["idrac_firmware"] = manager.get("FirmwareVersion", "Unknown")
            result["idrac_model"] = manager.get("Model", "Unknown")

            # System BIOS
            system = await self._request("/redfish/v1/Systems/System.Embedded.1/")
            result["bios_version"] = system.get("BiosVersion", "Unknown")

            # Dell OEM update service
            try:
                update_service = await self._request("/redfish/v1/UpdateService/")
                result["update_service"] = update_service
            except IdracError:
                pass

            # Dell component firmware (iDRAC8+)
            try:
                dell_update = await self._request(
                    "/redfish/v1/UpdateService/Oem/Dell/DellUpdate/"
                )
                result["dell_update"] = dell_update
            except IdracError:
                pass

        except IdracError as e:
            logger.error(f"Failed to get firmware info from {self.ip}: {e}")

        return result

    async def get_alerts_settings(self) -> dict:
        """Get alert/notification settings."""
        result = {}
        try:
            # Email alerts
            try:
                result["email"] = await self._request(
                    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellAlertsService/DellAlertsService/"
                )
            except IdracError:
                result["email"] = {}

            # SNMP
            try:
                result["snmp"] = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/NetworkProtocol/")
            except IdracError:
                result["snmp"] = {}

        except IdracError as e:
            logger.error(f"Failed to get alerts settings from {self.ip}: {e}")

        return result

    async def get_virtual_media(self) -> dict:
        """Get virtual media status."""
        try:
            media = await self._request("/redfish/v1/Systems/System.Embedded.1/VirtualMedia/")
            return media
        except IdracError as e:
            logger.error(f"Failed to get virtual media from {self.ip}: {e}")
            return {}

    @property
    def drac_version(self) -> Optional[str]:
        return self._drac_version


class IdracError(Exception):
    """iDRAC API error."""
    pass


class IdracConnectionError(IdracError):
    """Connection to iDRAC failed."""
    pass
