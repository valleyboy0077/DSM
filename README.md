# Dell Server Manager (DSM)

**Unified out-of-band management for Dell PowerEdge servers via iDRAC.**

A Linux package that gives you a single pane of glass to monitor and control multiple Dell servers — temperatures, fans, power, and more. It also includes a **Model Context Protocol (MCP) server** so AI coding agents can query live hardware data and perform troubleshooting autonomously.

## Architecture

```
┌──────────────────────────────────────────────────┐
│                    React / TypeScript SPA         │
│              (dark theme, real-time UI)           │
├──────────────────────────────────────────────────┤
│                  FastAPI Backend                  │
│                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ /servers │ │ /sensors │ │  /fans   │          │
│  └──────────┘ └──────────┘ └──────────┘          │
│                                                    │
│  ┌──────────┐  ┌─────────────┐  ┌────────────┐   │
│  │ MCP Server│  │ SensorPoller│  │ FanControl │   │
│  │ (AI API)  │  │  (3s loop)  │  │ (stepped)  │   │
│  └──────────┘  └─────────────┘  └────────────┘   │
│                                                    │
│  ┌─────────────┐ ┌──────────────┐                  │
│  │ iDRAC7/8    │ │ SQLite DB    │                  │
│  │ Connector   │ │ (encrypted)  │                  │
│  │ Redfish+WSM │ └──────────────┘                  │
│  └─────────────┘                                    │
└──────────────────────────────────────────────────┘
```

## Tech Stack

| Layer       | Technology                           |
|-------------|--------------------------------------|
| Backend     | Python 3.11+, FastAPI, httpx         |
| Database    | SQLAlchemy 2.x + aiosqlite (SQLite)  |
| iDRAC       | Redfish REST + WS-Man SOAP + IPMI    |
| Encryption  | AES-256-CBC for stored passwords     |
| Frontend    | React 18 + TypeScript + Vite 5       |
| AI Interface| MCP (Model Context Protocol) server  |
| Packaging   | Python editable install, .deb target |

## Default Settings

| Setting              | Default      | Config Key                       |
|----------------------|-------------|----------------------------------|
| Poll interval        | 3 seconds   | `DSM_SENSOR_POLL_INTERVAL`       |
| CPU temp range       | 45–70 °C   | `default_cpu_temp_min/max`       |
| Disk temp range      | 32–45 °C   | `default_disk_temp_min/max`      |
| Backend port         | 8000       | `DSM_PORT`                       |
| MCP server port      | 8101       | `DSM_MCP_PORT`                   |
| Encryption key       | (change!)  | `DSM_ENCRYPTION_KEY`             |

## Agent Integration and Live Telemetry

DSM's MCP integration is designed for agent-driven diagnostics and control.

- **Primary MCP transport for Hermes:** `http://127.0.0.1:8101/mcp`
- **Live hardware telemetry:** MCP sensor tools prefer live iDRAC data from `GET /sensors/live/{server_id}` instead of stored sensor history
- **Fan control:** manual fan changes go through `POST /fans/{server_id}/control`
- **Historical data:** the sensor history API remains available for auditing and trends, but it is not the preferred source for agent reads
- **Verification:** Hermes can validate the integration with `hermes mcp test dsm`

For implementation details, see:
- `src/dsm/mcp/README.md` for the MCP server behavior and tool layout
- `src/dsm/api/README.md` for API routes, including the live sensor endpoint

## Quick Start

```bash
# 1. Clone and set up virtual environment
cd dsm
python3 -m venv venv
source venv/bin/activate
pip install -e .

# 2. Set encryption key (REQUIRED before first use)
export DSM_ENCRYPTION_KEY='a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4'

# 3. Create data directory
sudo mkdir -p /var/lib/dsm

# 4. Install frontend dependencies (once)
cd frontend
npm install
cd ..

# 5. Start DSM (builds the Vite frontend before launching Uvicorn)
./start.sh
```

## Database migrations

DSM upgrades its SQLite schema automatically during application startup. To run
the same safe, idempotent upgrade before a deployment, stop DSM and run:

```bash
python -m dsm.migrations
```

The command uses `DSM_DB_PATH` (or its normal default), records each completed
migration in `schema_migrations`, and refuses to start if the recorded history
or required schema is inconsistent. Migrations are additive; take a normal
SQLite backup before any production deployment.

## Directory Structure

- `src/dsm/` — Python backend package
- `src/dsm/api/` — FastAPI route modules
- `src/dsm/mcp/` — MCP server for AI agent integration
- `frontend/` — React + TypeScript SPA (Vite)

## Supported Controller Metadata and Profiles

DSM currently separates **displayed controller generation** from the stored
**DSM control profile**.

- Displayed controller generation comes from detected hardware metadata and is
  returned by the API as `controller_label`
- Stored DSM control profile still lives in `drac_version` / `controller_profile`
  and is used for compatibility routing in backend control paths

Examples from verified 13G hardware:
- `PowerEdge R530` displays as `iDRAC8 (13G)`
- `PowerEdge R730xd` displays as `iDRAC8 (13G)`

## Fan Control Notes

- Default manual override is **25%**
- For the verified Dell systems in this lab, **RPM movement** is the
  authoritative proof that manual override worked
- On the current stored-profile `idrac7` path, DSM prefers **Dell OEM IPMI** for
  live manual fan-speed changes and restoring automatic control
- `racadm` may be installed for diagnostics, but is not the preferred first-line
  live fan-speed control path on the verified hardware
- `controller_label` should be used for UI display; raw `drac_version` is a
  compatibility profile and can be misleading as a literal generation label
- See `src/dsm/FAN_CONTROL_DEBUGGING.md` for the full troubleshooting guide

## Target Servers

R320, R720xd, R730, R530, R730xd, R640, R740, and other 13G/14G PowerEdge models.
