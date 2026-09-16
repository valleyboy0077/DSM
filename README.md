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

For the canonical Git → test → Docker build → Compose deploy workflow, see
[Development](docs/development.md). Docker is the supported deployment artifact;
deployment requirements, persistence, stopping, and rollback are documented in
[Deployment](docs/deployment.md). Contributors should also read
[CONTRIBUTING.md](CONTRIBUTING.md).

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

## Container deployment

The short path is `make docker-build`, `make docker-config`, and `make docker-up`.
See [docs/development.md](docs/development.md) for the complete workflow and the
safe operator LAN override procedure.

The container image builds the Vite frontend during `docker build` and serves
the resulting assets through FastAPI. It starts Uvicorn directly; it does not
use the host `start.sh` or its `nohup` flow.

```bash
# 1. Clone the repository
git clone <repository-url> dsm
cd dsm

# 2. Supply the encryption key outside Git. It must be a unique 32-character
#    hexadecimal key; do not use the placeholder below as a real key.
cp .env.example .env
# Edit .env locally and set DSM_ENCRYPTION_KEY and
# DSM_BOOTSTRAP_ADMIN_PASSWORD to real, unique values.

# 3. Build and start DSM in the background
docker compose up --build -d

# 4. Confirm the service is healthy, then inspect logs if needed
curl http://127.0.0.1:8080/health
docker compose logs -f dsm
```

The application and MCP endpoint are bound to loopback by default:
`http://127.0.0.1:8080` and `http://127.0.0.1:8101/mcp`. Compose stores SQLite
at `/var/lib/dsm/dsm.db` in the named `dsm_data` volume, so data survives
recreating the container. Stop it with
`docker compose down`; include `--volumes` only when intentionally discarding
the persisted database.

The Compose environment explicitly sets `DSM_DB_PATH`, `DSM_ENCRYPTION_KEY`,
`DSM_HOST`, `DSM_PORT`, `DSM_DEBUG`, `DSM_MCP_ENABLED`, `DSM_MCP_PORT`, and
the bootstrap-admin variables. `DSM_ENCRYPTION_KEY` and
`DSM_BOOTSTRAP_ADMIN_PASSWORD` are required from the untracked `.env` file when
the persistent volume contains no users. The password is not logged. Never
commit real values, and preserve the same encryption key for as long as
encrypted stored passwords must remain readable.

To expose DSM remotely, keep Compose bound to loopback and put an authenticated
TLS reverse proxy in front of it, or deliberately change the port bindings and
restrict access with a host firewall. Do not publish the MCP port directly to
an untrusted network.

Frontend dependencies are locked in `frontend/package-lock.json` and the image
uses `npm ci`. Python dependencies currently use lower bounds in `pyproject.toml`
and this repository has no Python lock file, so the Python dependency resolution
at image-build time is not fully reproducible.

To publish an image after validating it locally, tag and push the image to your
chosen registry (replace the placeholder registry path):

```bash
docker compose build
docker tag dell-server-manager:local registry.example.com/your-org/dell-server-manager:latest
docker push registry.example.com/your-org/dell-server-manager:latest
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
