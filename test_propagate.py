#!/usr/bin/env python3
"""Test propagation of operator1 with privilege fields."""
import httpx
import json

BASE = "http://127.0.0.1:8080"
client = httpx.Client(base_url=BASE, timeout=120, verify=False)

# Login
r = client.post("/auth/login", json={"username":"admin","password":"admin"})
token = r.json().get("access_token", "")
headers = {"Authorization": f"Bearer {token}"}

# Get servers
r = client.get("/servers/", headers=headers)
servers = r.json()
print(f"Servers: {json.dumps(servers, indent=2)}")

# Get operator1
r = client.get("/users/", headers=headers)
users = r.json()
operator1 = None
for u in users:
    if u["username"] == "operator1":
        operator1 = u
        break
print(f"operator1: {json.dumps(operator1, indent=2)}")

if not operator1:
    print("ERROR: operator1 not found")
    exit(1)

# Propagate operator1
print("\n--- Propagating operator1 ---")
r = client.post(f"/users/{operator1['id']}/propagate", headers=headers)
print(f"Status: {r.status_code}")
print(f"Response: {json.dumps(r.json(), indent=2)}")

# Check iDRAC users
if r.status_code == 200:
    prop_data = r.json()
    for result in prop_data.get("results", []):
        if result.get("status") == "success":
            server_id = result["server_id"]
            print(f"\n--- Checking iDRAC users for server {server_id} ---")
            r2 = client.get(f"/settings/idrac-users/{server_id}", headers=headers)
            print(f"Status: {r2.status_code}")
            if r2.status_code == 200:
                idrac_users = r2.json()
                operator1_found = None
                for u in idrac_users:
                    if u.get("username") == "operator1":
                        operator1_found = u
                        break
                if operator1_found:
                    print(f"\noperator1 found in iDRAC:")
                    print(f"  RoleId: {operator1_found.get('role')}")
                    print(f"  Privileges: {operator1_found.get('privileges')}")
                    print(f"  LAN Privilege: {operator1_found.get('lan_privilege')}")
                    print(f"  Serial Privilege: {operator1_found.get('serial_privilege')}")
                    print(f"  Access: {operator1_found.get('access')}")
                else:
                    print(f"\noperator1 NOT found in iDRAC users")
                    print(f"iDRAC users: {json.dumps(idrac_users, indent=2)}")
            else:
                print(f"Failed to list iDRAC users: {r2.text[:200]}")

client.close()
