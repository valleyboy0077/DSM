#!/usr/bin/env python3
"""Smoke-test the running DSM API: sensors, fan config, server detail.

Logs in against a local DSM instance and dumps three representative API
responses. Defaults target the factory bootstrap account documented in the
README quickstart (username and login value ``admin``); for any other
account, override via environment variables:

    DSM_URL=http://127.0.0.1:8080 DSM_USER=... DSM_PASSWORD='...' \
        python check_sensors.py

Never point this script at production credentials.
"""
import json
import os

import httpx

# Bootstrap login defaults [user, login] from the development quickstart.
DEFAULT_LOGIN = ("admin", "admin")

DSM_URL = os.getenv("DSM_URL", "http://127.0.0.1:8080")
login_user = os.getenv("DSM_USER", DEFAULT_LOGIN[0])
login_value = os.getenv("DSM_PASSWORD", DEFAULT_LOGIN[1])
SERVER_ID = os.getenv("DSM_SERVER_ID", "1")

# Login and keep the bearer token for the API calls below.
resp = httpx.post(
    f"{DSM_URL}/auth/login",
    json={"username": login_user, "password": login_value, "grant_type": "password"},
)
resp.raise_for_status()
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

print("=== All Sensors ===")
resp = httpx.get(f"{DSM_URL}/sensors/?server_id={SERVER_ID}", headers=headers)
print(json.dumps(resp.json(), indent=2))

print("\n=== Fan Config ===")
resp = httpx.get(f"{DSM_URL}/fans/{SERVER_ID}", headers=headers)
print(json.dumps(resp.json(), indent=2))

print("\n=== Server Info ===")
resp = httpx.get(f"{DSM_URL}/servers/{SERVER_ID}", headers=headers)
print(json.dumps(resp.json(), indent=2))
