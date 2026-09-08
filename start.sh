#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
: "${DSM_ENCRYPTION_KEY:?DSM_ENCRYPTION_KEY must be supplied by the operator}"
export DSM_PORT="${DSM_PORT:-8080}"

if [[ -x .venv/bin/python3 ]]; then
    PYTHON=.venv/bin/python3
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
else
    echo "Error: no Python interpreter found. Expected .venv/bin/python3 or python3 on PATH." >&2
    exit 1
fi

# Vite writes the hashed assets served by FastAPI to src/dsm/frontend. Build
# before starting Uvicorn so index.html never references a missing bundle.
bash ./build.sh

PYTHONPATH=src nohup "$PYTHON" -m uvicorn dsm.app:app --host 0.0.0.0 --port "$DSM_PORT" > /tmp/dsm.log 2>&1 &
echo "PID: $!"
sleep 3
curl -s "http://127.0.0.1:$DSM_PORT/health"
