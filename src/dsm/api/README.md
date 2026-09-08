# DSM API Routes

FastAPI REST endpoints for the Dell Server Manager.

## Endpoints

### Health

| Method | Path       | Description              |
|--------|-----------|--------------------------|
| GET    | `/health` | Health check + version   |
| GET    | `/info`   | System info (Python, OS) |

### Servers

| Method | Path                    | Description                                          |
|--------|------------------------|------------------------------------------------------|
| GET    | `/servers/`            | List all servers with control-profile + display metadata |
| POST   | `/servers/`            | Add a new server                                     |
| GET    | `/servers/{id}`        | Get server details                                   |
| PUT    | `/servers/{id}`        | Update server                                        |
| DELETE | `/servers/{id}`        | Remove server                                        |
| POST   | `/servers/{id}/power`  | Power control (on/off/etc.)                          |

Server metadata notes:
- `drac_version` is the legacy/internal DSM control profile
- `controller_profile` is the preferred alias for that stored profile
- `controller_label` is the user-facing display value for UI pages
- `controller_source` explains how that display label was derived

### Sensors

| Method    | Path                        | Description                          |
|-----------|-----------------------------|--------------------------------------|
| GET       | `/sensors/`                 | Historical readings (with filters)   |
| GET       | `/sensors/live/{id}`        | Live temperatures + fans + metadata  |
| GET       | `/sensors/latest`           | Latest reading per sensor label      |
| GET       | `/sensors/summary/{id}`     | Current temp summary per server      |
| WebSocket | `/sensors/ws/{id}`          | Real-time sensor push stream         |

### Fans

| Method | Path                     | Description                        |
|--------|--------------------------|------------------------------------|
| GET    | `/fans/{server_id}`      | Get fan config for a server        |
| GET    | `/fans/{server_id}/telemetry` | Live RPM/PWM fan telemetry   |
| PUT    | `/fans/{server_id}`      | Update fan config                  |
| POST   | `/fans/{server_id}/control` | Execute fan action                |

Fan actions: `set_manual` (with speed), `set_auto`, `reset`, `run_cycle`

Notes:
- direct control actions now persist the effective mode back to `fan_configs`
- default manual override is `25`
- see `../FAN_CONTROL_DEBUGGING.md` for protocol-selection and hardware notes

## File Structure

| File       | Routes                                    |
|------------|-------------------------------------------|
| `health.py`| `/health`, `/info`                        |
| `servers.py`| Server CRUD + power control              |
| `sensors.py`| Sensor queries + WebSocket               |
| `fans.py`  | Fan config + control actions              |

## Modifying Routes

Each module exports a `router = APIRouter(...)` instance. Register in `app.py`:

```python
app.include_router(my_router)
```

All routes use `Depends(get_session)` for database access.
