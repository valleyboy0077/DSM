# MCP Server — AI Agent Interface

Model Context Protocol server for Dell Server Manager.
It exposes hardware monitoring and control as MCP tools that AI agents can call programmatically.

## Purpose

Bridge between LLM agents and physical Dell PowerEdge server hardware.
Agents can query sensors, read live fan telemetry, change fan modes, and manage power without needing to know Redfish/iDRAC internals.

## Current State

Implemented and tested.
The MCP server is started from the DSM app lifespan and is available at:

- Streamable HTTP: `http://127.0.0.1:8101/mcp`
- SSE helper endpoint: `http://127.0.0.1:8101/sse` for low-level MCP clients

For Hermes CLI integration, use the streamable HTTP endpoint (`/mcp`).
The CLI `hermes mcp add` flow expects streamable HTTP, and testing `/sse` directly can yield a `405 Method Not Allowed` during connection checks.

## Runtime configuration

The MCP server reads these environment variables:

- `DSM_API_URL` — base URL for the DSM REST API, defaults to `http://127.0.0.1:<port>`
- `DSM_API_TOKEN` — optional bearer token for authenticated API calls

If unset, the MCP server talks to the local DSM API process.

## Tooling overview

### Inventory / diagnostics
- `list_servers` — list all managed Dell servers
- `get_server(server_id)` — server details by ID
- `health_check()` — DSM API health
- `server_summary(server_id)` — combined server, fan, and sensor snapshot

### Sensors
- `get_temperatures(server_id)` — live temperatures from iDRAC
- `get_fan_speeds(server_id)` — live fan RPM/PWM from iDRAC
- `get_fan_telemetry(server_id)` — live fan telemetry payload
- `get_all_sensors(server_id)` — live temperatures + fan telemetry + system metadata

### Fan control
- `get_fan_config(server_id)` — persisted fan config and current target
- `set_fan_mode(server_id, mode, auto_control=True)` — switch auto/manual/profile
- `set_fan_speed(server_id, speed_percent)` — set manual fan speed
- `get_temp_profile(server_id)` — current temperature thresholds
- `set_temp_profile(server_id, ...)` — update thresholds

### Power control
- `power_control(server_id, action)` — on/off/restart/shutdown/push_button
- `get_power_state(server_id)` — current power state

### Inventory management
- `add_server(name, ipmi_ip, ipmi_user, ipmi_password)`
- `remove_server(server_id)`

## Live-data preference

The MCP server prefers *live hardware reads* over stored sensor history when it is answering agent-facing sensor requests.
This is important for fan monitoring because history-backed sensor rows can lag behind the current hardware state.

Recommended live paths:

- `GET /sensors/live/{server_id}` — live temperatures + fans + system info
- `GET /fans/{server_id}/telemetry` — live fan RPM / percent values

Stored history is still available through the normal `/sensors/` endpoints for auditing and trend analysis.

## Fan-control note

Fan control actions go through the action endpoint, not a config-only patch.
The correct backend path is:

- `POST /fans/{server_id}/control` with one of:
  - `{"action": "set_manual", "speed": 25}`
  - `{"action": "set_auto"}`
  - `{"action": "reset"}`
  - `{"action": "run_cycle"}`

A direct `PATCH /fans/{server_id}` call may look plausible but is not the MCP server's control path.

## Hermes CLI setup

Add the MCP server like this:

```bash
hermes mcp add dsm --url http://127.0.0.1:8101/mcp
hermes mcp test dsm
```

## Notes

- The MCP server is launched by the DSM application lifespan when `settings.mcp_enabled` is true.
- The implementation reuses the existing DSM API rather than duplicating logic.
- Live fan telemetry is exposed separately from stored sensor history to avoid confusing stale readings with current hardware state.
