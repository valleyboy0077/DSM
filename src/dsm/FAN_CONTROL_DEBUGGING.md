# DSM Fan Control and Controller Metadata Troubleshooting

This document records the live-tested behavior for DSM fan control and the UI
metadata fixes that were required to keep Dell controller information accurate.

Use this as the first troubleshooting note before changing fan-control protocol
selection, server metadata rendering, or browser caching.

## 1. Two different controller concepts exist in DSM

DSM currently carries two related but different values:

- **DSM control profile**
  - stored in the database field `servers.drac_version`
  - values are currently `idrac7`, `idrac8`, or `unknown`
  - used internally to choose the fan-control/backend compatibility path
- **Displayed controller generation**
  - exposed by the API as `controller_label` and `controller_source`
  - intended for humans in Command Center, Infrastructure, and Fan Control
  - derived from the detected PowerEdge platform model when possible

### Why this distinction matters

The stored `drac_version` field is no longer a trustworthy user-facing label for
exact hardware identity.

Example from live systems:
- `PowerEdge R530` is a **13G** platform and should display as **iDRAC8 (13G)**
- `PowerEdge R730xd` is a **13G** platform and should display as **iDRAC8 (13G)**
- both systems may still keep `drac_version=idrac7` internally because DSM uses
  that stored value as the current compatibility/control profile

If the UI renders raw `drac_version` directly, users will incorrectly see
`iDRAC7` even when the hardware should be shown as `iDRAC8 (13G)`.

## 2. Canonical UI/API fields

### API fields

`GET /servers/` and `GET /servers/{id}` should be treated like this:

- `drac_version`
  - legacy/internal control profile
- `controller_profile`
  - preferred alias for the same stored control profile
- `controller_label`
  - user-facing label such as `iDRAC8 (13G)`
- `controller_source`
  - explanation of how the label was derived

### Frontend rule

UI pages should prefer:
1. `controller_label`
2. `controller_source`

Only fall back to local inference if those backend fields are absent.

## 3. Current live-tested fan-control split

DSM does **not** use one protocol for every Dell controller generation.

### Current control routing

- **stored profile `idrac7`**
  - first choice: **Dell OEM IPMI raw fan commands**
  - fallback: **racadm thermal settings**
  - used on the verified R730xd/R530 compatibility path
- **stored profile `idrac8`**
  - first choice: **Dell Redfish thermal actions**
- **WS-Man**
  - retained as a telemetry / legacy helper path
  - not the primary live control method for the verified manual-override fix

## 4. Why the old UI showed the wrong controller version

There were two root causes:

1. the frontend rendered raw `drac_version` as if it were exact hardware
   generation
2. browsers could keep serving an older SPA shell after rebuilds, so the UI
   still showed the old logic even after code changes

### Fixes applied

- backend now returns `controller_label` and `controller_source`
- frontend prefers those backend fields over raw `drac_version`
- FastAPI serves `index.html` with:
  - `Cache-Control: no-store, max-age=0`
- this prevents stale SPA shells from pointing at older JS bundles after rebuilds

## 5. Manual override persistence expectations

Direct control actions must persist state to `fan_configs` so the API and UI
still match the real hardware state after a button press.

Expected persistence:

- `set_manual`
  - `mode=manual`
  - `auto_control=false`
  - `manual_speed=<requested percent>`
- `set_auto`
  - `mode=auto`
  - `auto_control=true`
- `reset`
  - `mode=profile`
  - `auto_control=true`

Default `manual_speed` is **25**.

## 6. Safe verification procedure

For this environment, routine validation must stay at **25% or lower**.
Do **not** test above **25%** unless explicitly requested.

### Recommended re-test flow

1. confirm the server starts in **auto** mode
2. capture baseline live fan telemetry
3. apply manual override at **25%**
4. wait briefly for fan settle
5. confirm fan RPMs move
6. refresh the page and confirm persisted config still shows manual 25%
7. return the server to **auto** mode
8. confirm RPMs settle back toward baseline

### Leave the server in auto mode

After any debugging session, the target server should be returned to **auto**.

## 7. Authoritative success signal for R730xd/R530 testing

For the verified Dell systems in this environment, **RPM movement** is the real
proof that manual override worked.

Do **not** rely only on the PWM/percent display when troubleshooting.

### Why

On some Dell telemetry paths, especially WS-Man/DCIM-backed fan telemetry, the
reported fan percentage can remain stale during manual override even though the
hardware command succeeded.

### Practical rule

Treat the following as authoritative:
- persisted `manual_speed`
- live RPM movement
- successful return to auto mode

Treat displayed PWM/percent as helpful but potentially stale.

## 8. Known verified behavior

### R730xd

Verified live behavior:
- auto baseline median RPM: `3840`
- manual 25% peak median RPM: `5640`
- returned-to-auto median RPM: `3840`

### R530

Verified live behavior:
- auto baseline median RPM: `3000`
- manual 25% peak median RPM: `5520`
- returned-to-auto median RPM: `3060`

## 9. Common failure patterns

### UI shows `iDRAC7` on Command Center or Infrastructure

Check:
- `GET /servers/` response for `controller_label`
- whether the page is rendering raw `drac_version` instead of `controller_label`
- whether the browser is still holding an old SPA shell
- the response headers for `/` and SPA routes should include:
  - `Cache-Control: no-store, max-age=0`

### API says success but fans do not move

Check:
- stored control profile on the server record
- whether the active backend path is IPMI/racadm or Redfish
- whether the request hit the current backend process
- live RPM telemetry, not just saved config

### Config changes but UI still shows old values

Check:
- whether the action path persisted `fan_configs`
- whether the frontend refetched after the control action
- whether a stale SPA bundle is still loaded

### racadm appears to work but fan RPM does not change reliably

This was already observed on the verified Dell systems.
Do not switch the live path back to racadm-only control without re-proving it on
hardware.

## 10. Files to inspect first

- `src/dsm/api/fans.py`
  - persistence and fan-control REST behavior
- `src/dsm/fan_control.py`
  - control-path selection and runtime fan actions
- `src/dsm/idrac_connector.py`
  - Redfish, IPMI, WS-Man, and racadm helpers
- `src/dsm/api/servers.py`
  - controller metadata shaping for API consumers
- `frontend/src/serverMetadata.ts`
  - frontend fallback label derivation
- `frontend/src/pages/Dashboard.tsx`
  - Command Center metadata rendering
- `frontend/src/pages/Inventory.tsx`
  - Infrastructure metadata rendering and add-server form wording
- `frontend/src/pages/FanControl.tsx`
  - controller display plus stale-telemetry warnings
- `src/dsm/app.py`
  - SPA no-store headers to avoid stale bundles

## 11. Fast smoke-test checklist

When future changes touch controller display or fan control, verify all of this:

- `GET /servers/` returns `controller_label`
- R530 displays `iDRAC8 (13G)`
- R730xd displays `iDRAC8 (13G)`
- Add-server form still labels the editable field as **DSM control profile**
- manual override default remains **25%**
- manual 25% changes RPMs on target hardware
- both servers are returned to **auto** after testing
- `/` response includes `Cache-Control: no-store, max-age=0`
