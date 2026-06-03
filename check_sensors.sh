#!/bin/bash
set -e

# Login
TOKEN=*** -X POST http://127.0.0.1:8080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin","grant_type":"password"}')

echo "=== All Sensors ==="
curl -s "http://127.0.0.1:8080/sensors/?server_id=1" \
  -H "Authorization: Bearer *** | python3 -m json.tool

echo ""
echo "=== Fan Config ==="
curl -s http://127.0.0.1:8080/fans/1 \
  -H "Authorization: Bearer *** | python3 -m json.tool
