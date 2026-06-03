#!/usr/bin/env python3
"""API integration test — matches actual endpoints."""
import httpx
import json

BASE = "http://127.0.0.1:8080"
errors = []
passes = []

def check(name, condition):
    if condition:
        passes.append(name)
    else:
        errors.append(name)

client = httpx.Client(base_url=BASE, timeout=30, verify=False)

# 1. Health
r = client.get("/health")
check("Health endpoint", r.status_code == 200 and r.json()["status"] == "healthy")

# 2. Info
r = client.get("/info")
check("Info endpoint", r.status_code == 200)

# 3. Auth
r = client.post("/auth/login", json={"username":"admin","password":"admin","grant_type":"password"})
check("Admin login", r.status_code == 200)
token = r.json().get("access_token", "")
check("Got JWT token", len(token) > 0)
headers = {"Authorization": f"Bearer {token}"}

# 4. Auth enforcement (no token on protected routes)
r = client.get("/servers/")
check("Servers requires auth", r.status_code == 401)
r = client.get("/sensors/")
check("Sensors requires auth", r.status_code == 401)
r = client.get("/fans/1")
check("Fans requires auth", r.status_code == 401)

# 5. Add server
r = client.post("/servers/", json={
    "name": "R730xd-Test",
    "ipmi_ip": "10.1.1.109",
    "ipmi_user": "ned",
    "ipmi_password": "Nclaw!",
}, headers=headers)
check("Add server", r.status_code == 201)
server_id = r.json().get("id", 1)

# 6. List servers
r = client.get("/servers/", headers=headers)
check("List servers", r.status_code == 200 and len(r.json()) >= 1)

# 7. Get server details
r = client.get(f"/servers/{server_id}", headers=headers)
check("Get server", r.status_code == 200)

# 8. Sensors (need to wait for poller)
import time
time.sleep(8)
r = client.get("/sensors/", headers=headers)
check("Sensors endpoint", r.status_code == 200)

r = client.get(f"/sensors/latest?server_id={server_id}", headers=headers)
check("Latest sensors", r.status_code == 200)

r = client.get(f"/sensors/summary/{server_id}", headers=headers)
check("Sensor summary", r.status_code == 200)

# 9. Fans
r = client.get(f"/fans/{server_id}", headers=headers)
check("Get fan config", r.status_code == 200)
fan_mode = r.json().get("mode", "")
check("Fan mode default is auto", fan_mode == "auto")

# 10. Update fan config
r = client.put(f"/fans/{server_id}", json={
    "mode": "auto",
    "cpu_temp_min": 48,
    "cpu_temp_max": 72,
}, headers=headers)
check("Update fan config", r.status_code == 200)
check("Fan mode after update", r.json().get("mode") == "auto")

# 11. Settings endpoints (sub-routes)
r = client.get(f"/settings/network/{server_id}", headers=headers)
check("Settings network", r.status_code in (200, 502))  # 502 if iDRAC endpoint fails

# 12. Temp profiles (requires server_id)
r = client.get(f"/temp-profiles/server/{server_id}", headers=headers)
check("List temp profiles", r.status_code == 200)

# 13. Users
r = client.get("/users/", headers=headers)
check("List users", r.status_code == 200 and len(r.json()) >= 1)

# 14. Groups
r = client.get("/groups/", headers=headers)
check("List groups", r.status_code == 200)

# 15. Users create
r = client.post("/users/", json={
    "username": "operator1",
    "password": "oper123456",
    "email": "op@localhost",
    "role": "operator",
    "propagate_to_idrac": False,
}, headers=headers)
if r.status_code != 201:
    print(f"  DEBUG create user: {r.status_code} {r.text[:200]}")
check("Create operator user", r.status_code == 201)

# 16. Delete server
r = client.delete(f"/servers/{server_id}", headers=headers)
check("Delete server", r.status_code == 204)

# 17. Verify iDRAC data (from DB)
r = client.get(f"/servers/{server_id}", headers=headers)
# Server was deleted, should be 404
check("Deleted server returns 404", r.status_code == 404)

# Report
print(f"\n{'='*50}")
print(f"Results: {len(passes)} passed, {len(errors)} failed out of {len(passes)+len(errors)}")
print(f"{'='*50}")
if errors:
    print("\n❌ FAILURES:")
    for e in errors:
        print(f"   - {e}")
else:
    print("\n✅ ALL TESTS PASSED")
for p in passes:
    print(f"   ✓ {p}")

client.close()
exit(1 if errors else 0)
