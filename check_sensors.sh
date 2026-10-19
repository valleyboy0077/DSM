#!/bin/bash
# Smoke-test a running local DSM instance with curl: sensors, fan config.
#
# Defaults target the development bootstrap account (admin/admin, see
# README). Override without editing the script:
#   DSM_URL=http://host:port DSM_USER=... DSM_PASSWORD='...' ./check_sensors.sh
# Requires curl and python3 (used only to pretty-print JSON).
set -euo pipefail

DSM_URL="${DSM_URL:-http://127.0.0.1:8080}"
DSM_USER="${DSM_USER:-admin}"
DSM_PASSWORD="${DSM_PASSWORD:-admin}"
SERVER_ID="${DSM_SERVER_ID:-1}"

# Log in and extract the bearer token from the JSON response.
TOKEN=$(curl -sS -X POST "$DSM_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$DSM_USER\",\"password\":\"$DSM_PASSWORD\",\"grant_type\":\"password\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

echo "=== All Sensors ==="
curl -sS "$DSM_URL/sensors/?server_id=$SERVER_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo ""
echo "=== Fan Config ==="
curl -sS "$DSM_URL/fans/$SERVER_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
