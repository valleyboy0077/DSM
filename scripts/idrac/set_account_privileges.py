#!/usr/bin/env python3
"""PATCH privilege fields on an iDRAC account slot (write operation!).

Originally used to grant Operator privileges to the ``operator1`` account on
the R730xd (iDRAC7, account slot id=4). iDRAC7 ignores unknown properties, so
the body carries every privilege spelling the firmware might honour: the
Redfish ``Privileges`` list plus Dell's legacy ``MaximumLANUserPrivilegeGranted``
/ ``MaximumSerialPortUserPrivilegeGranted`` / ``Access`` fields and the
numeric ``RoleId``.

The new account password is supplied via ``IDRAC_ACCOUNT_PASSWORD`` (never
hardcoded). After patching, the script re-reads the account and prints the
resulting configuration so the change can be verified immediately.

Usage::

    export IDRAC_HOST=... IDRAC_USER=... IDRAC_PASSWORD=...
    export IDRAC_ACCOUNT_PASSWORD='...new password...'
    python scripts/idrac/set_account_privileges.py --account 4 --username operator1

Credentials come from environment variables; see ``redfish_common.py``.
"""

from __future__ import annotations

import argparse
import os
import sys

from redfish_common import build_client

# Map of Dell RoleId numbers used by iDRAC7 firmware.
ROLE_IDS = {
    "admin": "500",      # Administrator
    "operator": "501",   # Operator
    "readonly": "502",   # User / read-only
    "none": "503",       # No access
}


def main() -> None:
    """Patch one account slot with Operator-class privileges, then verify."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", help="iDRAC host (default: $IDRAC_HOST)")
    parser.add_argument("--user", help="iDRAC login (default: $IDRAC_USER)")
    parser.add_argument("--password", help="iDRAC login password (default: $IDRAC_PASSWORD)")
    parser.add_argument("--account", required=True, help="account slot id, e.g. 4")
    parser.add_argument("--username", required=True, help="existing username of that slot")
    parser.add_argument(
        "--role",
        default="operator",
        choices=sorted(ROLE_IDS),
        help="role to assign (default: operator)",
    )
    args = parser.parse_args()

    account_password = os.environ.get("IDRAC_ACCOUNT_PASSWORD")
    if not account_password:
        sys.exit("Set IDRAC_ACCOUNT_PASSWORD to the new password for the target account.")

    client = build_client(args.host, args.user, args.password)
    path = f"/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/{args.account}"
    body = {
        "UserName": args.username,
        "Password": account_password,
        "Enabled": True,
        "RoleId": ROLE_IDS[args.role],
        "Privileges": ["Login", "ConfigureComponents", "ConfigureSelf", "RemoteConsole", "VirtualMedia"],
        "MaximumLANUserPrivilegeGranted": args.role.capitalize(),
        "MaximumSerialPortUserPrivilegeGranted": args.role.capitalize(),
        "Access": args.role.capitalize(),
    }

    try:
        response = client.patch(path, json=body)
        print(f"Update status: {response.status_code}")
        print(f"Response: {response.text[:500]}")

        verify = client.get(path)
        if verify.status_code == 200:
            data = verify.json()
            print(f"\nUpdated {data.get('UserName')}:")
            print(f"  RoleId:       {data.get('RoleId')}")
            print(f"  Privileges:   {data.get('Privileges')}")
            print(f"  LAN Priv:     {data.get('MaximumLANUserPrivilegeGranted')}")
            print(f"  Serial Priv:  {data.get('MaximumSerialPortUserPrivilegeGranted')}")
            print(f"  Access:       {data.get('Access')}")
            print(f"  Enabled:      {data.get('Enabled')}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
