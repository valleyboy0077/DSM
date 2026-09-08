#!/usr/bin/env python3
"""Test iDRAC7 user creation with string RoleId."""
import httpx
import json

client = httpx.Client(
    base_url="https://10.1.1.109",
    auth=httpx.BasicAuth("ned", "Nclaw!"),
    verify=False,
    timeout=30,
)

# Try updating operator1 (id=4) with string RoleId
body = {
    "Password": "oper123456",
    "Enabled": True,
    "RoleId": "Operator",
}

r = client.patch("/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/4", json=body)
print(f"Update status: {r.status_code}")
print(f"Response: {r.text[:500]}")

# Verify
r2 = client.get("/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/4")
if r2.status_code == 200:
    d = r2.json()
    print(f"\nUpdated operator1:")
    print(f"  UserName: {d.get('UserName')}")
    print(f"  RoleId: {d.get('RoleId')}")
    print(f"  Privileges: {d.get('Privileges')}")
    print(f"  LAN Priv: {d.get('MaximumLANUserPrivilegeGranted')}")
    print(f"  Serial Priv: {d.get('MaximumSerialPortUserPrivilegeGranted')}")
    print(f"  Access: {d.get('Access')}")
    print(f"  Enabled: {d.get('Enabled')}")

client.close()
