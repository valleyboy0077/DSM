#!/bin/bash
cd /home/sarah/dsm-scaffold
export DSM_ENCRYPTION_KEY="0123456789abcdef0123456789abcdef"
DSM_PORT="${DSM_PORT:-8080}"
PYTHONPATH=src nohup python3 -m uvicorn dsm.app:app --host 0.0.0.0 --port "$DSM_PORT" > /tmp/dsm.log 2>&1 &
echo "PID: $!"
sleep 4
echo "=== Health ==="
curl -s "http://127.0.0.1:$DSM_PORT/health"
echo ""
echo "=== Log ==="
cat /tmp/dsm.log
