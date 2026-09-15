import types
from datetime import datetime, timedelta, timezone

import pytest

from dsm.sensor_poller import SensorPoller
from dsm.idrac_connector import FanSensor, TempSensor


class FakeConnector:
    def __init__(self):
        self.drac_version = "idrac8"
        self.ip = "10.0.0.1"
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
async def test_auto_control_uses_observed_pwm_not_cached_command_target(monkeypatch):
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

    # The prior request was 14%, but the chassis is currently at 22% PWM.
    # Cooling decisions must move from measured hardware duty, not from the
    # previous command, so a lower-speed command cannot accidentally be issued
    # while the fans are already running faster.
    poller._last_fan_control_target[1] = 14

    result = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, sensor_data)

    assert result["current_fan_percent"] == 22
    assert result["target_fan_percent"] == 19
    assert connector.calls == [("Manual", 19)]


@pytest.mark.asyncio
async def test_auto_control_steps_up_from_observed_pwm_when_cached_target_is_lower(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    server = types.SimpleNamespace(id=1)
    fan_config = types.SimpleNamespace(
        auto_control=True,
        polling_seconds=20,
        manual_speed=14,
        cpu_temp_min=35.0,
        cpu_temp_max=70.0,
        disk_temp_min=32.0,
        disk_temp_max=45.0,
    )
    sensor_data = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=75.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=27, health="OK")],
    )
    poller._last_fan_control_target[1] = 14

    async def fake_profile_ranges(session, server_id):
        return []

    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", fake_profile_ranges)

    result = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, sensor_data)

    assert result["current_fan_percent"] == 27
    assert result["target_fan_percent"] == 39
    assert connector.calls == [("Manual", 39)]


@pytest.mark.asyncio
async def test_auto_control_refreshes_ipmi_keepalive_target_from_observed_pwm(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    connector.drac_version = "idrac7"
    connector._fan_control_backend = "ipmi"
    server = types.SimpleNamespace(id=1, name="R730xd")
    fan_config = types.SimpleNamespace(
        auto_control=True,
        polling_seconds=20,
        manual_speed=14,
        cpu_temp_min=35.0,
        cpu_temp_max=70.0,
        disk_temp_min=32.0,
        disk_temp_max=45.0,
    )
    sensor_data = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=50.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=22, health="OK")],
    )
    poller._last_fan_control_target[1] = 14

    async def fake_profile_ranges(session, server_id):
        return []

    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", fake_profile_ranges)

    result = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, sensor_data)

    assert result["action_taken"] == "unchanged"
    assert poller._last_fan_control_target[1] == 22

    connector.calls.clear()

    class FakeSessionForRows:
        async def execute(self, query):
            return types.SimpleNamespace(all=lambda: [(server, types.SimpleNamespace())])

    class FakeSessionContext:
        async def __aenter__(self):
            return FakeSessionForRows()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("dsm.sensor_poller.async_session", lambda: FakeSessionContext())

    async def fake_get_connector(_server):
        return connector

    monkeypatch.setattr(poller, "_get_connector", fake_get_connector)

    await poller._refresh_auto_control_targets_once()

    assert connector.calls == [("Manual", 22)]


@pytest.mark.asyncio
async def test_auto_control_keeps_cached_target_when_no_usable_pwm_exists(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    server = types.SimpleNamespace(id=1, name="R730xd")
    fan_config = types.SimpleNamespace(
        auto_control=True,
        polling_seconds=20,
        manual_speed=14,
        cpu_temp_min=35.0,
        cpu_temp_max=70.0,
        disk_temp_min=32.0,
        disk_temp_max=45.0,
    )
    sensor_data = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=50.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=50, percent_source="rpm_estimate")],
    )
    poller._last_fan_control_target[1] = 14

    async def fake_profile_ranges(session, server_id):
        return []

    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", fake_profile_ranges)

    result = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, sensor_data)

    assert result["current_fan_percent"] == 14
    assert poller._last_fan_control_target[1] == 14


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


def test_temperature_rate_is_calculated_per_server_sensor_from_timestamps():
    poller = SensorPoller()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sensor = TempSensor(name="CPU1 Temp", value_celsius=50.0, physical_context="CPU")

    assert poller._temperature_rates(1, [sensor], start) == {}

    warmer = TempSensor(name="CPU1 Temp", value_celsius=54.0, physical_context="CPU")
    rates = poller._temperature_rates(1, [warmer], start + timedelta(seconds=8))

    assert rates == {"cpu1 temp|cpu": 0.5}


@pytest.mark.asyncio
async def test_rapid_cpu_rise_near_max_gets_fast_bounded_response(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    server = types.SimpleNamespace(id=1)
    fan_config = types.SimpleNamespace(auto_control=True, cpu_temp_min=35.0, cpu_temp_max=70.0,
                                       disk_temp_min=32.0, disk_temp_max=45.0)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def profile_ranges(*_args):
        return []

    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", profile_ranges)
    first = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=68.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=20, health="OK")],
    )
    second = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=70.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=20, health="OK")],
    )

    await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, first, now=start)
    result = await poller._maybe_auto_control_fans(
        FakeSession(fan_config), server, connector, second, now=start + timedelta(seconds=2)
    )

    assert result["temperature_rates_c_per_sec"] == {"cpu1 temp|cpu": 1.0}
    assert result["target_fan_percent"] == 31
    assert connector.calls[-1] == ("Manual", 31)


@pytest.mark.asyncio
async def test_cooldown_is_damped_and_rising_temperature_blocks_fall():
    from dsm.fan_control import FanController

    controller = FanController(FakeConnector(), cpu_temp_min=40.0, cpu_temp_max=70.0)
    cool = {"cpu": [TempSensor(name="CPU1 Temp", value_celsius=32.0, physical_context="CPU")], "disk": []}
    target, _ = controller._incremental_fan_logic(cool, 30)
    assert target == 27  # asymmetric fall slew cap

    rising_cool = {"cpu": [TempSensor(name="CPU1 Temp", value_celsius=32.0, physical_context="CPU")], "disk": []}
    target, _ = controller._incremental_fan_logic(rising_cool, 30, temperature_rates={"cpu1 temp|cpu": 0.2})
    assert target == 30


@pytest.mark.asyncio
async def test_stable_in_range_temperature_converges_without_command_churn(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    server = types.SimpleNamespace(id=1)
    fan_config = types.SimpleNamespace(auto_control=True, cpu_temp_min=35.0, cpu_temp_max=70.0,
                                       disk_temp_min=32.0, disk_temp_max=45.0)
    sensor_data = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=50.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=22, health="OK")],
    )
    async def profile_ranges(*_args):
        return []
    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", profile_ranges)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    first = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, sensor_data, now=start)
    second = await poller._maybe_auto_control_fans(
        FakeSession(fan_config), server, connector, sensor_data, now=start + timedelta(seconds=20)
    )

    assert first["target_fan_percent"] == second["target_fan_percent"] == 22
    assert first["action_taken"] == second["action_taken"] == "unchanged"
    assert connector.calls == []


@pytest.mark.asyncio
async def test_minimum_command_interval_prevents_repeat_hot_commands(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    server = types.SimpleNamespace(id=1)
    fan_config = types.SimpleNamespace(auto_control=True, cpu_temp_min=35.0, cpu_temp_max=70.0,
                                       disk_temp_min=32.0, disk_temp_max=45.0)
    sensor_data = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=75.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=27, health="OK")],
    )
    async def profile_ranges(*_args):
        return []
    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", profile_ranges)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, sensor_data, now=start)
    result = await poller._maybe_auto_control_fans(
        FakeSession(fan_config), server, connector, sensor_data, now=start + timedelta(seconds=5)
    )

    assert result["action_taken"] == "unchanged"
    assert "Minimum automatic command interval" in result["reason"]
    assert connector.calls == [("Manual", 39)]
