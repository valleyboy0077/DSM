# DSM Frontend

React + TypeScript web UI for Dell Server Manager. Single-page app with dark theme, built with Vite.

## Tech Stack

- **React 18** — UI framework
- **TypeScript 5** — type safety
- **Vite 5** — dev server + production build
- No external UI library — custom CSS with CSS variables (dark theme)

## Development

```bash
cd frontend
npm install
npm run dev          # starts on :5173, proxies /api to FastAPI at :8000
npm run build        # outputs to ../src/dsm/frontend/ (served by FastAPI)
npm run preview      # preview production build locally
```

## Architecture

### File Structure

| File | Purpose |
|------|---------|
| `src/App.tsx` | Main application — server list, detail view, add server modal, fan control panel, power panel |
| `src/api.ts` | HTTP client wrapping all FastAPI endpoints |
| `src/types.ts` | TypeScript interfaces: `Server`, `SensorReading`, `FanConfig`, `FanControlResult`, `SensorSummary` |

### API Layer

`api.ts` exposes a named API object with methods for every backend endpoint:

- **Health:** `health()`
- **Servers:** `listServers()`, `addServer()`, `getServer()`, `updateServer()`, `deleteServer()`, `powerServer()`
- **Sensors:** `getSensors()`, `getSensorSummary(serverId)`
- **Fans:** `getFanConfig()`, `updateFanConfig()`, `controlFans()`

All calls go through a shared `request<T>()` helper that handles JSON parsing and error extraction.

### UI Components (all in App.tsx)

- **Server list** — grid of cards showing name, model, status, last poll time
- **ServerDetail** — per-server view with sensor cards, power control, fan control, full sensor table
- **AddServerModal** — form for name + iDRAC IP + credentials
- **FanPanel** — current config display + buttons for PID cycle, auto/manual modes, preset speeds (50/75/100%)
- **PowerPanel** — on/off/restart/shutdown buttons
- **StatusBadge** — colored status indicator (green/yellow/red)

### Vite Proxy

During development, Vite proxies `/api`, `/health`, `/info`, `/servers`, `/sensors`, `/fans` to `http://localhost:8000` (FastAPI). This avoids CORS issues.

### Production Build

`vite build` outputs static files to `../src/dsm/frontend/` — served directly by FastAPI as static files.
