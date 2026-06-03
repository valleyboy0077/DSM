#!/bin/bash
cd /home/sarah/dsm-scaffold
export DSM_ENCRYPTION_KEY="0123456789abcdef0123456789abcdef"
PYTHONPATH=src python3 -m uvicorn dsm.app:app --host 0.0.0.0 --port 8080 2>&1
