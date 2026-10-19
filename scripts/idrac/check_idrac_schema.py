#!/usr/bin/env python3
"""Inspect which account privilege fields an iDRAC7 controller supports.

Used during the iDRAC7 user-propagation work: Dell's iDRAC7 Redfish schema
exposes different privilege properties than iDRAC8+, so before PATCHing an
account we dump the ``Accounts`` schema and a full sample account (the root
slot, id=2) to see exactly which fields the firmware returns and accepts.

Usage::

    export IDRAC_HOST=... IDRAC_USER=... IDRAC_PASSWORD=...
    python scripts/idrac/check_idrac_schema.py [HOST] [--user U] [--password P]

Credentials come from environment variables; see ``redfish_common.py``.
"""

from __future__ import annotations

import argparse
import json

from redfish_common import build_client


def main() -> None:
    """Print the Accounts property schema and one fully-populated account."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", help="iDRAC host (default: $IDRAC_HOST)")
    parser.add_argument("--user", help="iDRAC user (default: $IDRAC_USER)")
    parser.add_argument("--password", help="iDRAC password (default: $IDRAC_PASSWORD)")
    args = parser.parse_args()

    client = build_client(args.host, args.user, args.password)
    try:
        schema = client.get("/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/$schema")
        print(f"Schema: {schema.text[:500]}")

        account = client.get("/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/2")
        if account.status_code == 200:
            print("\nFull account data for root (id=2):")
            print(json.dumps(account.json(), indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
