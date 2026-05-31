# Dell Server Manager (DSM)

**Unified out-of-band management for Dell PowerEdge servers via iDRAC.**

A Linux package that gives you a single pane of glass to monitor and control multiple Dell servers — temperatures, fans, power, and more. Includes a **Model Context Protocol (MCP) server** so AI coding agents can query hardware data and perform troubleshooting autonomously.

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
│  │ (AI API)  │  │  (3s loop)  │  │  (PID)     │   │
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
| Backend     | Python 3.13, FastAPI, aiohttp        |
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
| Min fan speed        | 20%        | `pid_fan_min`                    |
| Max fan speed        | 100%       | `pid_fan_max`                    |
| Backend port         | 8000       | `DSM_PORT`                       |
| MCP server port      | 8101       | `DSM_MCP_PORT`                   |
| Encryption key       | (change!)  | `DSM_ENCRYPTION_KEY`             |

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

# 4. Start backend
uvicorn dsm.app:app --host 0.0.0.0 --port 8100

# 5. Build frontend (separate terminal)
cd frontend
npm install && npm run build
```

## Directory Structure

- `src/dsm/` — Python backend package
- `src/dsm/api/` — FastAPI route modules
- `src/dsm/mcp/` — MCP server for AI agent integration
- `frontend/` — React + TypeScript SPA (Vite)

## Supported iDRAC Versions

- **iDRAC7** (R720, R730, etc.) — WS-Man SOAP primary, limited Redfish
- **iDRAC8** (R640, R740, etc.) — Full Redfish REST

## Target Servers

R320, R720xd, R730, R530, R730xd, R640, R740, and other 13G/14G PowerEdge models.
