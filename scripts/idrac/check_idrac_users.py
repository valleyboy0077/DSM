#!/usr/bin/env python3
"""List the populated user accounts on an iDRAC controller via Redfish.

Diagnostics helper used while integrating iDRAC7 (R730xd) account management
with DSM. It walks the ``Managers/iDRAC.Embedded.1/Accounts`` collection and
prints, for each non-empty slot: id, username, role id, enabled state and the
legacy privilege fields (Privileges, MaximumLANUserPrivilegeGranted,
MaximumSerialPortUserPrivilegeGranted, Access).

Usage::

    export IDRAC_HOST=... IDRAC_USER=... IDRAC_PASSWORD=...
    python scripts/idrac/check_idrac_users.py [HOST] [--user U] [--password P]

Credentials come from environment variables; see ``redfish_common.py``.
"""

from __future__ import annotations

import argparse

from redfish_common import build_client


def main() -> None:
    """Query the account collection and print one summary line per user."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", help="iDRAC host (default: $IDRAC_HOST)")
    parser.add_argument("--user", help="iDRAC user (default: $IDRAC_USER)")
    parser.add_argument("--password", help="iDRAC password (default: $IDRAC_PASSWORD)")
    args = parser.parse_args()

    client = build_client(args.host, args.user, args.password)
    try:
        response = client.get("/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/")
        response.raise_for_status()
        for member in response.json().get("Members", []):
            path = member.get("@odata.id", "")
            if not path:
                continue
            account = client.get(path)
            if account.status_code != 200:
                continue
            data = account.json()
            username = data.get("UserName", "")
            if not username:  # Empty slot -- nothing configured here.
                continue
            print(
                f"  {data.get('Id')}: {username} "
                f"(RoleId={data.get('RoleId')}, Enabled={data.get('Enabled')})"
            )
            print(f"    Privileges: {data.get('Privileges', [])}")
            print(f"    LAN Priv:   {data.get('MaximumLANUserPrivilegeGranted', '')}")
            print(f"    Serial Priv:{data.get('MaximumSerialPortUserPrivilegeGranted', '')}")
            print(f"    Access:     {data.get('Access', '')}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
