# MCP Server — AI Agent Interface

Model Context Protocol server for Dell Server Manager. Exposes hardware monitoring and control as MCP tools that AI agents can call programmatically.

## Purpose

Bridge between LLM agents and physical Dell PowerEdge server hardware. Agents can query sensors, control fans, and manage power without needing to know Redfish/iDRAC internals.

## Current State

Scaffold only — `__init__.py` is a placeholder. The MCP server has not yet been implemented.

## Planned Design

- **Transport:** Stdio MCP (compatible with Claude Code, Codex, etc.)
- **Tools to expose:**
  - `list_servers` — list all managed Dell servers
  - `get_sensor_summary(server_id)` — current temps/status
  - `get_fan_config(server_id)` — current fan mode and thresholds
  - `control_fans(server_id, action, speed?)` — run PID cycle, set manual/auto
  - `power_server(server_id, action)` — on/off/restart/shutdown
  - `add_server(name, ipmi_ip, user, password)` — register new server
- **Reuses** existing modules: `idrac_connector`, `sensor_poller`, `fan_control`

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Package init (placeholder — MCP server not yet implemented) |

## Dependencies on other modules

- `idrac_connector` — Redfish/iDRAC communication
- `fan_control` — PID fan logic, threshold management
- `sensor_poller` — async sensor collection
- `models`, `database` — SQLAlchemy ORM + SQLite persistence
