# DSM Backend Package

Core Python modules for the Dell Server Manager backend.

## Modules

| File                | Purpose                                                     |
|---------------------|-------------------------------------------------------------|
| `__init__.py`       | Package init, version string (`0.1.0`)                      |
| `app.py`            | FastAPI app factory, lifespan handler, router registration   |
| `config.py`         | Pydantic settings with env var support (`DSM_*` prefix)     |
| `database.py`       | Async SQLAlchemy engine, session factory, table init         |
| `models.py`         | ORM models: `Server`, `SensorReading`, `FanConfig`          |
| `crypto.py`         | AES-256-CBC encryption/decryption for iDRAC passwords       |
| `idrac_connector.py`| iDRAC client (Redfish, WS-Man, IPMI, and racadm helpers)    |
| `fan_control.py`    | Rule-based fan speed controller + protocol selection        |
| `sensor_poller.py`  | Background polling loop, WebSocket broadcaster              |
| `api/`              | FastAPI route modules (see `api/README.md`)                 |
| `mcp/`              | MCP server for AI agent integration (see `mcp/README.md`)   |

## Data Flow

1. `SensorPoller` runs every 3s (configurable)
2. For each registered `Server`, calls `IdracConnector.get_sensors()`
3. Temperature/fan readings stored as `SensorReading` rows
4. Fan controller evaluates temps vs thresholds, adjusts fan speed
5. Results broadcast via WebSocket to connected UI clients

## Fan Control and Controller Metadata Debugging

See `FAN_CONTROL_DEBUGGING.md` for:
- live-tested fan-control protocol choices
- controller-profile vs displayed-controller semantics
- safe 25%-only manual verification steps
- stale-SPA/cache troubleshooting notes for the WebUI

## Configuration

All settings via `DSM_` prefixed env vars or `.env` file:

```
DSM_SENSOR_POLL_INTERVAL=3
DSM_DB_PATH=/var/lib/dsm/dsm.db
DSM_ENCRYPTION_KEY=abcdef1234567890...  # 32 hex chars
DSM_PORT=8080
DSM_DEBUG=true
```
