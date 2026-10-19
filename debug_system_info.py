#!/usr/bin/env python3
"""Debug script: dump what the DSM iDRAC connector returns for one server.

Hands-on check of ``dsm.idrac_connector.IdracConnector`` against a live
controller: version detection, system info, temperature sensors and fans.
Credentials are taken from the environment (never hardcoded):

    export IDRAC_HOST=10.1.1.109 IDRAC_USER=ned IDRAC_PASSWORD='...'
    python debug_system_info.py

The connector talks Redfish over HTTPS to the iDRAC; run from the repo root
(or set PYTHONPATH=src) so ``dsm`` is importable.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dsm.idrac_connector import IdracConnector


def _require_env(name: str) -> str:
    """Return an environment variable or exit with a helpful message."""
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Set {name} first (see module docstring for usage).")
    return value


async def main():
    """Connect to the iDRAC and print detected version, sensors and fans."""
    connector = IdracConnector(
        ip=_require_env("IDRAC_HOST"),
        username=_require_env("IDRAC_USER"),
        password=_require_env("IDRAC_PASSWORD"),
    )

    print("=== detect_version() ===")
    info = await connector.detect_version()
    print(f"  model: {info.model}")
    print(f"  service_tag: {info.service_tag}")
    print(f"  drac_version: {info.drac_version}")
    print(f"  power_state: {info.power_state}")

    print("\n=== get_sensors() ===")
    data = await connector.get_sensors()
    print(f"  system_info: {data.system_info}")
    if data.system_info:
        print(f"    model: {data.system_info.model}")
        print(f"    service_tag: {data.system_info.service_tag}")
        print(f"    power_state: {data.system_info.power_state}")
    print(f"  temperatures: {len(data.temperatures)}")
    for t in data.temperatures:
        print(f"    {t.name}: {t.value_celsius}C ({t.physical_context})")
    print(f"  fans: {len(data.fans)}")
    for f in data.fans:
        print(f"    {f.name}: {f.rpm} RPM")


asyncio.run(main())
