#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
: "${DSM_ENCRYPTION_KEY:?DSM_ENCRYPTION_KEY must be supplied by the operator}"
export DSM_PORT="${DSM_PORT:-8080}"

bash ./build.sh

PYTHONPATH=src python3 -m uvicorn dsm.app:app --host 0.0.0.0 --port "$DSM_PORT" 2>&1
