#!/usr/bin/env python3
"""Debug script to check what iDRAC returns for system info."""
import asyncio
import sys
sys.path.insert(0, "/home/sarah/dsm-scaffold/src")

from dsm.idrac_connector import IdracConnector

async def main():
    connector = IdracConnector(
        ip="10.1.1.109",
        username="ned",
        password="Nclaw!",
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
