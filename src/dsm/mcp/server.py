"""MCP server implementation — registers all DSM tools for AI agents.

Each @mcp.tool function is exposed as a callable tool that an MCP client
(Claude Desktop, Codex, etc.) can invoke with natural-language arguments.

The server communicates with the DSM REST API via HTTP, so it can run as a
separate process without sharing memory with the FastAPI app.
"""

import json
import logging
from urllib.parse import urljoin

import httpx
from mcp.server.fastmcp import FastMCP

from dsm.config import settings

logger = logging.getLogger(__name__)

# ─── Configuration ──────────────────────────────────────────────────────

# Environment variables override defaults
import os
API_URL = os.environ.get("DSM_API_URL", f"http://127.0.0.1:{settings.port}")
API_TOKEN = os.environ.get("DSM_API_TOKEN", "")

# ─── MCP instance ───────────────────────────────────────────────────────

mcp = FastMCP(
    "Dell Server Manager",
    instructions=(
        "Unified out-of-band management for Dell PowerEdge servers via iDRAC. "
        "Query hardware data, monitor temperatures, control fans, manage servers."
    ),
)

# ─── HTTP helper ────────────────────────────────────────────────────────


async def _api(path: str, method: str = "GET", json_body=None) -> dict:
    """Make an authenticated request to the DSM REST API."""
    headers = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method,
            urljoin(API_URL, path),
            json=json_body,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


# ─── Tool: Server Inventory ────────────────────────────────────────────


@mcp.tool()
async def list_servers() -> str:
    """List all managed Dell servers with their current status.

    Returns a JSON array of servers with fields:
    id, name, ipmi_ip, ipmi_user, drac_version, model, serial, status, last_seen, added_at
    """
    data = await _api("/servers/")
    return json.dumps(data, indent=2)


@mcp.tool()
async def get_server(server_id: int) -> str:
    """Get details for a specific server by ID.

    Args:
        server_id: The numeric ID of the server (from list_servers).
    """
    data = await _api(f"/servers/{server_id}")
    return json.dumps(data, indent=2)


@mcp.tool()
async def add_server(name: str, ipmi_ip: str, ipmi_user: str, ipmi_password: str) -> str:
    """Add a new server to manage.

    The password is encrypted at rest using AES-256-CBC.

    Args:
        name: Human-friendly server name (e.g., 'R730xd').
        ipmi_ip: iDRAC/IPMI management IP address.
        ipmi_user: iDRAC username.
        ipmi_password: iDRAC password (will be encrypted).
    """
    data = await _api(
        "/servers/",
        method="POST",
        json_body={
            "name": name,
            "ipmi_ip": ipmi_ip,
            "ipmi_user": ipmi_user,
            "ipmi_password": ipmi_password,
        },
    )
    return json.dumps(data, indent=2)


@mcp.tool()
async def remove_server(server_id: int) -> str:
    """Remove a server and all its associated sensor data.

    Args:
        server_id: The numeric ID of the server to remove.
    """
    await _api(f"/servers/{server_id}", method="DELETE")
    return f"Server {server_id} removed successfully."


# ─── Tool: Sensors ─────────────────────────────────────────────────────


@mcp.tool()
async def get_temperatures(server_id: int) -> str:
    """Get current temperature sensor readings for a server.

    Returns CPU, disk, and ambient temperatures in Celsius.

    Args:
        server_id: The numeric ID of the server.
    """
    data = await _api(f"/sensors/?server_id={server_id}&sensor_type=cpu")
    return json.dumps(data, indent=2)


@mcp.tool()
async def get_fan_speeds(server_id: int) -> str:
    """Get current fan speed readings for a server.

    Returns RPM and percentage for all fans.

    Args:
        server_id: The numeric ID of the server.
    """
    data = await _api(f"/sensors/?server_id={server_id}&sensor_type=fan")
    return json.dumps(data, indent=2)


@mcp.tool()
async def get_all_sensors(server_id: int) -> str:
    """Get all sensor readings (temperatures + fans) for a server.

    Args:
        server_id: The numeric ID of the server.
    """
    temps = await get_temperatures(server_id)
    fans = await get_fan_speeds(server_id)
    return json.dumps({
        "server_id": server_id,
        "temperatures": json.loads(temps),
        "fans": json.loads(fans),
    }, indent=2)


# ─── Tool: Fan Control ─────────────────────────────────────────────────


@mcp.tool()
async def get_fan_config(server_id: int) -> str:
    """Get the current fan control configuration for a server.

    Returns mode (auto/manual/profile), CPU/disk temp ranges,
    manual speed percentage, and auto-control toggle.

    Args:
        server_id: The numeric ID of the server.
    """
    data = await _api(f"/fans/{server_id}")
    return json.dumps(data, indent=2)


@mcp.tool()
async def set_fan_mode(server_id: int, mode: str, auto_control: bool = True) -> str:
    """Set the fan control mode for a server.

    Args:
        server_id: The numeric ID of the server.
        mode: One of 'auto', 'manual', 'profile'.
        auto_control: Whether automatic fan control is enabled.
    """
    valid_modes = {"auto", "manual", "profile"}
    if mode not in valid_modes:
        return f"Error: mode must be one of {valid_modes}"

    data = await _api(
        f"/fans/{server_id}",
        method="PATCH",
        json_body={"mode": mode, "auto_control": auto_control},
    )
    return json.dumps(data, indent=2)


@mcp.tool()
async def set_fan_speed(server_id: int, speed_percent: int) -> str:
    """Set the manual fan speed percentage for a server.

    Only takes effect when mode is 'manual'.

    Args:
        server_id: The numeric ID of the server.
        speed_percent: Fan speed from 1 to 100.
    """
    clamped = max(1, min(100, speed_percent))
    data = await _api(
        f"/fans/{server_id}",
        method="PATCH",
        json_body={"mode": "manual", "manual_speed": clamped},
    )
    return json.dumps(data, indent=2)


# ─── Tool: Temperature Profiles ────────────────────────────────────────


@mcp.tool()
async def get_temp_profile(server_id: int) -> str:
    """Get the temperature profile for a server.

    Returns CPU and disk temperature thresholds used by the fan controller.

    Args:
        server_id: The numeric ID of the server.
    """
    data = await _api(f"/temp-profiles/{server_id}")
    return json.dumps(data, indent=2)


@mcp.tool()
async def set_temp_profile(
    server_id: int,
    cpu_temp_min: float = 45.0,
    cpu_temp_max: float = 70.0,
    disk_temp_min: float = 32.0,
    disk_temp_max: float = 45.0,
) -> str:
    """Set temperature thresholds for fan control.

    Args:
        server_id: The numeric ID of the server.
        cpu_temp_min: Minimum CPU temperature for fan engagement (°C).
        cpu_temp_max: Maximum CPU temperature before max fan (°C).
        disk_temp_min: Minimum disk temperature threshold (°C).
        disk_temp_max: Maximum disk temperature threshold (°C).
    """
    data = await _api(
        f"/temp-profiles/{server_id}",
        method="PATCH",
        json_body={
            "cpu_temp_min": cpu_temp_min,
            "cpu_temp_max": cpu_temp_max,
            "disk_temp_min": disk_temp_min,
            "disk_temp_max": disk_temp_max,
        },
    )
    return json.dumps(data, indent=2)


# ─── Tool: Power Control ───────────────────────────────────────────────


@mcp.tool()
async def power_control(server_id: int, action: str) -> str:
    """Send a power control command to the server.

    Args:
        server_id: The numeric ID of the server.
        action: One of 'on', 'off', 'restart', 'shutdown', 'push_button'.
    """
    valid_actions = {"on", "off", "restart", "shutdown", "push_button"}
    if action not in valid_actions:
        return f"Error: action must be one of {valid_actions}"

    data = await _api(
        f"/servers/{server_id}/power",
        method="POST",
        json_body={"action": action},
    )
    return json.dumps(data, indent=2)


@mcp.tool()
async def get_power_state(server_id: int) -> str:
    """Get the current power state of a server.

    Args:
        server_id: The numeric ID of the server.
    """
    server = await _api(f"/servers/{server_id}")
    return json.dumps({
        "server_id": server_id,
        "name": server.get("name"),
        "status": server.get("status"),
    }, indent=2)


# ─── Tool: Health Check ────────────────────────────────────────────────


@mcp.tool()
async def health_check() -> str:
    """Check if the DSM API is healthy and responsive.

    Returns the API status, version, and timestamp.
    """
    data = await _api("/health")
    return json.dumps(data, indent=2)


# ─── Tool: System Summary ──────────────────────────────────────────────


@mcp.tool()
async def server_summary(server_id: int) -> str:
    """Get a comprehensive summary of a server including
    current temperatures, fan speeds, and configuration.

    This is the best starting point for diagnostics — it combines
    server info, temperatures, fans, and fan config in one call.

    Args:
        server_id: The numeric ID of the server.
    """
    server = await _api(f"/servers/{server_id}")
    fan_config = {}
    temps = []
    fans = []

    try:
        fan_config = await _api(f"/fans/{server_id}")
    except Exception:
        pass

    try:
        temps = await _api(f"/sensors/?server_id={server_id}&sensor_type=cpu")
    except Exception:
        pass

    try:
        fans = await _api(f"/sensors/?server_id={server_id}&sensor_type=fan")
    except Exception:
        pass

    return json.dumps({
        "server": server,
        "fan_config": fan_config,
        "temperatures": temps,
        "fans": fans,
    }, indent=2)


# ─── Entry Point ───────────────────────────────────────────────────────


def main():
    """Run the MCP server via SSE transport."""
    mcp_port = settings.mcp_port
    logger.info(f"Starting DSM MCP server on port {mcp_port}")
    mcp.run("sse", host="0.0.0.0", port=mcp_port)


if __name__ == "__main__":
    main()
