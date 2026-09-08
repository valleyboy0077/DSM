import types
from datetime import datetime, timedelta, timezone

import pytest

from dsm.sensor_poller import SensorPoller
from dsm.idrac_connector import FanSensor, TempSensor


class FakeConnector:
    def __init__(self):
        self.drac_version = "idrac8"
        self.calls = []
        self._fan_control_backend = "redfish"

    async def set_fan_mode_redfish(self, mode, speed_percent=None):
        self.calls.append((mode, speed_percent))
        return True

    async def set_fan_mode_ipmi(self, mode, speed_percent=None):
        self.calls.append((mode, speed_percent))
        self._fan_control_backend = "ipmi"
        return True


class FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalars(self):
        return self

    def first(self):
        return self._obj


class FakeSession:
    def __init__(self, fan_config):
        self.fan_config = fan_config

    async def execute(self, query):
        return FakeResult(self.fan_config)


@pytest.mark.asyncio
async def test_auto_control_ramps_down_across_cycles_even_when_live_pwm_is_stale(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    server = types.SimpleNamespace(id=1)
    fan_config = types.SimpleNamespace(
        auto_control=True,
        polling_seconds=20,
        manual_speed=25,
        cpu_temp_min=35.0,
        cpu_temp_max=70.0,
        disk_temp_min=32.0,
        disk_temp_max=45.0,
    )
    sensor_data = types.SimpleNamespace(
        temperatures=[
            TempSensor(name="CPU1 Temp", value_celsius=29.0, physical_context="CPU"),
            TempSensor(name="Disk Bay 1", value_celsius=28.0, physical_context="Drive"),
            TempSensor(name="Inlet", value_celsius=40.0, physical_context="SystemBoard"),
        ],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=22, health="OK")],
    )

    async def fake_profile_ranges(session, server_id):
        return []

    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", fake_profile_ranges)

    targets = []
    for _ in range(6):
        result = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, sensor_data)
        targets.append(result["target_fan_percent"])

    assert targets == [19, 16, 13, 10, 7, 7]
    assert connector.calls == [("Manual", 19), ("Manual", 16), ("Manual", 13), ("Manual", 10), ("Manual", 7)]


@pytest.mark.asyncio
async def test_auto_keepalive_refreshes_cached_ipmi_target(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    connector.drac_version = 'idrac7'
    connector._fan_control_backend = 'ipmi'
    poller._last_fan_control_target[1] = 7

    server = types.SimpleNamespace(id=1, name='R730xd')
    config = types.SimpleNamespace(auto_control=True, mode='auto')

    class FakeSessionForRows:
        async def execute(self, query):
            return types.SimpleNamespace(all=lambda: [(server, config)])

    class FakeSessionContext:
        async def __aenter__(self):
            return FakeSessionForRows()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr('dsm.sensor_poller.async_session', lambda: FakeSessionContext())

    async def fake_get_connector(server_obj):
        return connector

    monkeypatch.setattr(poller, '_get_connector', fake_get_connector)

    await poller._refresh_auto_control_targets_once()

    assert connector.calls == [('Manual', 7)]
