#!/bin/bash
cd /home/sarah/dsm-scaffold
export DSM_ENCRYPTION_KEY="0123456789abcdef0123456789abcdef"
PYTHONPATH=src nohup python3 -m uvicorn dsm.app:app --host 0.0.0.0 --port 8080 > /tmp/dsm.log 2>&1 &
echo "PID: $!"
sleep 3
curl -s http://127.0.0.1:8080/health
