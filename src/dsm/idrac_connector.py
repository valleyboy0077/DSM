"""iDRAC connector — dual protocol support (Redfish REST + WS-Man SOAP).

Handles authentication, sensor reading, fan control, and power operations
for both iDRAC7 (WS-Man primary) and iDRAC8 (Redfish primary).
"""

import logging
import ssl
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx

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
    health: str = "OK"


@dataclass
class SystemInfo:
    """System identification info."""
    model: str
    service_tag: str
    bios_version: str
    firmware_version: str  # iDRAC firmware
    drac_version: str  # "idrac7" or "idrac8"
    power_state: str = "Unknown"


@dataclass
class IdracSensorData:
    """Complete sensor snapshot from an iDRAC."""
    system_info: Optional[SystemInfo] = None
    temperatures: list = field(default_factory=list)
    fans: list = field(default_factory=list)


class IdracConnector:
    """Connect to and manage a Dell iDRAC instance.

    Supports both iDRAC7 (WS-Man/SOAP, limited Redfish) and iDRAC8 (full Redfish).
    Auto-detects protocol support on first connection.
    """

    def __init__(self, ip: str, username: str, password: str):
        self.ip = ip
        self.username = username
        self.password = password
        self.base_url = f"https://{ip}"
        self._drac_version: Optional[str] = None
        self._firmware_version: Optional[str] = None
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
        """Auto-detect iDRAC version and return system info."""
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
            response = await self._request_wsman(xml)
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
        """Get all temperature and fan sensor readings."""
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
                fan_sensor = FanSensor(
                    name=fan.get("Name", "Unknown"),
                    rpm=fan.get("Reading", 0),
                    member_id=fan.get("MemberId", ""),
                    health=fan.get("Status", {}).get("Health", "Unknown"),
                )
                data.fans.append(fan_sensor)

            # Get system info if not already cached
            if not data.system_info:
                data.system_info = await self.detect_version()

        except IdracError as e:
            logger.error(f"Failed to get sensors from {self.ip}: {e}")

        return data

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

        if mode == "Manual" and speed_percent is not None:
            # Set fan speed via DCIM_CoolingUnitView
            xml = self._wsman_set_fan_speed(speed_percent)
        else:
            # Set fan mode
            xml = self._wsman_set_fan_mode(mode)

        try:
            await self._request_wsman(xml)
            return True
        except IdracError as e:
            logger.error(f"Failed to set fan mode via WS-Man: {e}")
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
              xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd"
              xmlns:n1="http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_ThermalService">
  <env:Header>
    <wsmid:ResourceURI xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd">
      http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_ThermalService:DCIM_ThermalService
    </wsmid:ResourceURI>
    <wsmid:Address xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd">
      CIMV2:DCIM_ThermalService.InstanceID="Dell Thermal Service"
    </wsmid:Address>
  </env:Header>
  <env:Body>
    <n1:DCIM_ThermalService_SetThermalPolicy>
      <n1:ThermalPolicy>{mode}</n1:ThermalPolicy>
    </n1:DCIM_ThermalService_SetThermalPolicy>
  </env:Body>
</env:Envelope>'''

    def _wsman_set_fan_speed(self, speed_percent: int) -> str:
        """WS-Man envelope for setting manual fan speed."""
        clamped = max(1, min(100, speed_percent))
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd"
              xmlns:n1="http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_CoolingUnitView">
  <env:Header>
    <wsmid:ResourceURI xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd">
      http://schemas.dell.com/wbem/wscim/1/cim-schema/2/DCIM_CoolingUnitView:DCIM_CoolingUnitView
    </wsmid:ResourceURI>
  </env:Header>
  <env:Body>
    <n1:DCIM_CoolingUnitView_SetDesiredSpeed>
      <n1:DesiredSpeed>{clamped}</n1:DesiredSpeed>
    </n1:DCIM_CoolingUnitView_SetDesiredSpeed>
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

    # ─── User Management ──────────────────────────────────────────────

    async def list_idrac_users(self) -> list[dict]:
        """List all iDRAC user accounts."""
        try:
            data = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/")
            users = []
            for account in data.get("Members", []):
                account_path = account.get("@odata.id", "")
                if account_path:
                    account_data = await self._request(account_path)
                    users.append({
                        "id": account_data.get("Id"),
                        "username": account_data.get("UserName", ""),
                        "enabled": account_data.get("Enabled", False),
                        "role": account_data.get("RoleId", ""),
                        "role_name": account_data.get("RoleName", ""),
                    })
            return users
        except IdracError:
            # iDRAC7 fallback — try Dell OEM endpoint
            try:
                data = await self._request("/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService/DellLCManager/")
                return []
            except IdracError:
                logger.warning(f"Cannot list users on {self.ip}")
                return []

    async def create_user(self, username: str, password: str, role: str = "ReadOnly") -> str:
        """Create a user on iDRAC. Returns status string."""
        try:
            # Find next available user slot
            users = await self.list_idrac_users()

            # Check if user already exists
            for u in users:
                if u["username"] == username:
                    # Update existing user
                    await self._request(
                        f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{u['id']}",
                        method="PATCH",
                        json_body={
                            "Password": password,
                            "Enabled": True,
                        },
                    )
                    return "success"

            # Find an empty slot (max 16 accounts, 0 and 1 are root/calvin)
            for i in range(2, 16):
                try:
                    account = await self._request(f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{i}")
                    if not account.get("UserName"):
                        # Empty slot found
                        body = {
                            "UserName": username,
                            "Password": password,
                            "Enabled": True,
                        }
                        # Map role name to RoleId
                        role_map = {
                            "Administrator": "500",
                            "Operator": "501",
                            "ReadOnly": "502",
                        }
                        body["RoleId"] = role_map.get(role, "502")
                        await self._request(
                            f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{i}",
                            method="PATCH",
                            json_body=body,
                        )
                        return "success"
                except IdracError:
                    continue

            return "failed"

        except IdracError as e:
            logger.error(f"Failed to create user {username} on {self.ip}: {e}")
            return "failed"

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
        """Get full hardware inventory: CPUs, memory, drives, PSUs, NICs."""
        result = {}
        try:
            system = await self._request("/redfish/v1/Systems/System.Embedded.1/")
            chassis = await self._request("/redfish/v1/Chassis/System.Embedded.1/")

            # Processors
            result["processors"] = system.get("Processors", [])

            # Memory
            memory = await self._request("/redfish/v1/Systems/System.Embedded.1/Memory/")
            result["memory"] = memory.get("Members", [])
            memory_details = []
            for m in memory.get("Members", []):
                try:
                    detail = await self._request(m.get("@odata.id", ""))
                    memory_details.append(detail)
                except IdracError:
                    pass
            result["memory_details"] = memory_details

            # Storage controllers
            storage = await self._request("/redfish/v1/Systems/System.Embedded.1/Storage/")
            result["storage_controllers"] = storage.get("Members", [])
            for s in storage.get("Members", []):
                try:
                    detail = await self._request(s.get("@odata.id", ""))
                    drives_path = detail.get("Drives", [])
                    drives_detail = []
                    for d in drives_path:
                        try:
                            drive = await self._request(d.get("@odata.id", ""))
                            drives_detail.append(drive)
                        except IdracError:
                            pass
                    result.setdefault("drives", []).extend(drives_detail)
                except IdracError:
                    pass

            # Power supplies
            result["power_supplies"] = chassis.get("PowerControls", [])

            # NICs
            nic_collection = await self._request("/redfish/v1/Systems/System.Embedded.1/EthernetInterfaces/")
            result["ethernet_interfaces"] = nic_collection.get("Members", [])

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
        """Get RAID/storage configuration."""
        result = {}
        try:
            storage = await self._request("/redfish/v1/Systems/System.Embedded.1/Storage/")
            controllers = []
            for s in storage.get("Members", []):
                try:
                    detail = await self._request(s.get("@odata.id", ""))
                    # Virtual disks
                    vd_path = detail.get("Volumes", [])
                    volumes = []
                    for v in vd_path:
                        try:
                            vol = await self._request(v.get("@odata.id", ""))
                            volumes.append(vol)
                        except IdracError:
                            pass
                    detail["volumes"] = volumes

                    # Physical drives
                    drives = []
                    for d in detail.get("Drives", []):
                        try:
                            drive = await self._request(d.get("@odata.id", ""))
                            drives.append(drive)
                        except IdracError:
                            pass
                    detail["drives"] = drives

                    controllers.append(detail)
                except IdracError:
                    pass
            result["controllers"] = controllers
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
