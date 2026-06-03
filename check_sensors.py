#!/usr/bin/env python3
import httpx
import json

# Login
resp = httpx.post("http://127.0.0.1:8080/auth/login", json={
    "username": "admin",
    "password": "admin",
    "grant_type": "password",
})
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

print("=== All Sensors ===")
resp = httpx.get("http://127.0.0.1:8080/sensors/?server_id=1", headers=headers)
print(json.dumps(resp.json(), indent=2))

print("\n=== Fan Config ===")
resp = httpx.get("http://127.0.0.1:8080/fans/1", headers=headers)
print(json.dumps(resp.json(), indent=2))

print("\n=== Server Info ===")
resp = httpx.get("http://127.0.0.1:8080/servers/1", headers=headers)
print(json.dumps(resp.json(), indent=2))
