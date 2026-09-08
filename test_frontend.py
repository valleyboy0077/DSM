#!/usr/bin/env python3
"""Comprehensive API + frontend integration test for DSM."""
import httpx
import sys

BASE = "http://127.0.0.1:8080"
errors = []
passes = []

def check(name, condition, detail=""):
    if condition:
        passes.append(f"  ✓ {name}")
    else:
        errors.append(f"  ✗ {name}: {detail}")

client = httpx.Client(base_url=BASE, timeout=10)

# ─── 1. Health Check ──────────────────────────────────────────
print("\n1. Health Check")
r = client.get("/health")
check("Health endpoint 200", r.status_code == 200, f"got {r.status_code}")
data = r.json()
check("Health status is 'healthy'", data.get("status") == "healthy")
check("Version present", "version" in data)

# ─── 2. Frontend SPA ──────────────────────────────────────────
print("\n2. Frontend SPA")
r = client.get("/")
check("Root returns 200", r.status_code == 200)
check("Root returns HTML", "text/html" in r.headers.get("content-type", ""))
check("index.html contains app root", '<div id="root"' in r.text, "no app root div")

# ─── 3. Login ─────────────────────────────────────────────────
print("\n3. Login")
r = client.post("/auth/login", json={
    "username": "admin",
    "password": "admin",
    "grant_type": "password",
})
check("Login returns 200", r.status_code == 200, f"got {r.status_code}")
token_data = r.json()
check("Login returns access_token", "access_token" in token_data)
token = token_data.get("access_token", "")
check("Token is non-empty", len(token) > 0)

headers = {"Authorization": f"Bearer {token}"}

# Wrong password
r = client.post("/auth/login", json={
    "username": "admin",
    "password": "wrong",
    "grant_type": "password",
})
check("Wrong password rejected", r.status_code == 401, f"got {r.status_code}")

# ─── 4. Servers API ───────────────────────────────────────────
print("\n4. Server Inventory")
r = client.get("/servers/", headers=headers)
check("List servers 200", r.status_code == 200, f"got {r.status_code}")
servers = r.json()
check("At least 1 server", len(servers) >= 1, f"got {len(servers)}")
if servers:
    s = servers[0]
    check("Server has name", s.get("name") == "R730xd")
    check("Server has model", s.get("model") == "PowerEdge R730xd")
    check("Server has serial", s.get("serial") == "51C4VG2")
    check("Server status online", s.get("status") == "online")
    check("Server iDRAC version", s.get("drac_version") == "idrac7")
    check("Server last_seen present", bool(s.get("last_seen")))

# Get single server
r = client.get(f"/servers/{s['id']}", headers=headers)
check("Get server by ID 200", r.status_code == 200)

# Add a test server
r = client.post("/servers/", headers=headers, json={
    "name": "TestServer",
    "ipmi_ip": "192.168.1.100",
    "ipmi_user": "test",
    "ipmi_password": "testpass",
})
check("Add server 201", r.status_code == 201, f"got {r.status_code}")
test_server = r.json()
test_id = test_server.get("id")
check("Added server has ID", test_id is not None)

# Update server
r = client.put(f"/servers/{test_id}", headers=headers, json={"name": "RenamedServer"})
check("Update server 200", r.status_code == 200)

# Delete test server
r = client.delete(f"/servers/{test_id}", headers=headers)
check("Delete server 204", r.status_code == 204, f"got {r.status_code}")

# ─── 5. Sensors API ──────────────────────────────────────────
print("\n5. Sensors")
r = client.get("/sensors/?server_id=1", headers=headers)
check("Sensors 200", r.status_code == 200)
sensor_data = r.json()
check("Has sensor readings", sensor_data.get("count", 0) > 0)
if sensor_data.get("readings"):
    latest = sensor_data["readings"][0]
    check("Reading has value", "value" in latest)
    check("Reading has sensor_type", latest.get("sensor_type") in ("cpu", "ambient", "disk"))
    check("Reading has label", bool(latest.get("sensor_label")))

# ─── 6. Fans API ──────────────────────────────────────────────
print("\n6. Fan Control")
r = client.get("/fans/1", headers=headers)
check("Fan config 200", r.status_code == 200)
fan = r.json()
check("Fan mode is 'auto'", fan.get("mode") == "auto")
check("Fan has server_id", fan.get("server_id") == 1)
check("Fan polling default is 20", fan.get("polling_seconds") == 20)
check("Fan config includes current_target_percent", "current_target_percent" in fan)

# Update fan mode
r = client.put("/fans/1", headers=headers, json={"mode": "manual", "manual_speed": 75})
check("Update fan mode 200", r.status_code == 200)
update_fan = r.json()
check("Fan mode changed to manual", update_fan.get("mode") == "manual")

# Reset to auto
r = client.put("/fans/1", headers=headers, json={"mode": "auto"})
check("Reset fan to auto 200", r.status_code == 200)

# ─── 7. Users API ─────────────────────────────────────────────
print("\n7. Users")
r = client.get("/users/", headers=headers)
check("List users 200", r.status_code == 200)
users = r.json()
check("At least 1 user (admin)", len(users) >= 1)
check("Admin user exists", any(u.get("username") == "admin" for u in users))

# ─── 8. Groups API ────────────────────────────────────────────
print("\n8. Groups/Roles")
r = client.get("/groups/", headers=headers)
check("List groups 200", r.status_code == 200)
groups = r.json()
check("At least 2 groups", len(groups) >= 2)

# ─── 9. Settings API ──────────────────────────────────────────
print("\n9. Settings")
r = client.get("/settings/", headers=headers)
check("Get settings 200", r.status_code == 200)
settings_data = r.json()
check("Has settings keys", len(settings_data) > 0)

# ─── 10. Temp Profiles API ────────────────────────────────────
print("\n10. Temperature Profiles")
r = client.get("/temp-profiles/", headers=headers)
check("List temp profiles 200", r.status_code == 200)

# ─── 11. Unauthenticated access ───────────────────────────────
print("\n11. Auth Protection")
r = client.get("/servers/", headers={})
check("Servers require auth", r.status_code in (401, 403), f"got {r.status_code}")
r = client.get("/fans/1", headers={})
check("Fans require auth", r.status_code in (401, 403), f"got {r.status_code}")

# ─── Summary ──────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"RESULTS: {len(passes)} passed, {len(errors)} failed")
print(f"{'='*50}")
for p in passes:
    print(p)
if errors:
    print("\nFAILURES:")
    for e in errors:
        print(e)
    sys.exit(1)
else:
    print("\nAll tests passed!")
    sys.exit(0)
