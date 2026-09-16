import asyncio
import types
from datetime import datetime, timedelta, timezone

import pytest

from dsm.config import settings
from dsm.fan_control import FanController
from dsm.idrac_connector import FanSensor, TempSensor
from dsm.sensor_poller import SensorPoller


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


class FakeMonotonicClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class PollServerSession:
    def __init__(self, servers):
        self.servers = servers

    async def execute(self, _query):
        return types.SimpleNamespace(scalars=lambda: types.SimpleNamespace(all=lambda: self.servers))


class PollServerSessionContext:
    def __init__(self, servers):
        self.session = PollServerSession(servers)

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class PersistingPollSession:
    def __init__(self, server):
        self.server = server
        self.readings = []
        self.commits = 0

    async def get(self, _model, _server_id):
        return self.server

    def add(self, reading):
        self.readings.append(reading)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        raise AssertionError("null temperature reading must not fail the poll")


class PersistingPollSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_poll_server_skips_null_temperature_readings_without_marking_server_offline(monkeypatch):
    poller = SensorPoller()
    server = types.SimpleNamespace(id=2, name="R720XD")
    session = PersistingPollSession(server)
    sensor_data = types.SimpleNamespace(
        system_info=None,
        temperatures=[
            TempSensor(name="CPU1 Temp", value_celsius=51.0, physical_context="CPU"),
            TempSensor(name="Exhaust Temp", value_celsius=None, physical_context="SystemBoard"),
        ],
        fans=[],
    )

    class Connector:
        async def get_sensors(self):
            return sensor_data

    async def get_connector(_server):
        return Connector()

    fan_control_inputs = []

    async def no_fan_control(*args):
        fan_control_inputs.append(args[3].temperatures)
        return None

    monkeypatch.setattr(poller, "_get_connector", get_connector)
    monkeypatch.setattr(
        "dsm.sensor_poller.async_session", lambda: PersistingPollSessionContext(session)
    )
    monkeypatch.setattr(poller, "_maybe_auto_control_fans", no_fan_control)

    result = await poller.poll_server(server)

    assert result["status"] == "ok"
    assert server.status == "online"
    assert [reading.value for reading in session.readings] == [51.0]
    assert [reading["value"] for reading in result["temperatures"]] == [51.0]
    assert fan_control_inputs == [[sensor_data.temperatures[0]]]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_poll_server_reports_powered_off_host_without_writing_telemetry(monkeypatch):
    poller = SensorPoller()
    last_seen = datetime(2026, 1, 1, tzinfo=timezone.utc)
    server = types.SimpleNamespace(
        id=2,
        name="R720XD",
        status="online",
        last_seen=last_seen,
        model=None,
        serial=None,
        drac_version="idrac7",
    )
    session = PersistingPollSession(server)
    sensor_data = types.SimpleNamespace(
        system_info=types.SimpleNamespace(power_state="Off"),
        temperatures=[
            TempSensor(name="CPU1 Temp", value_celsius=20.0, physical_context="CPU"),
            TempSensor(name="Exhaust Temp", value_celsius=28.0, physical_context="SystemBoard"),
            TempSensor(name="Inlet Temp", value_celsius=None, physical_context="SystemBoard"),
        ],
        fans=[],
    )

    class Connector:
        async def get_sensors(self):
            return sensor_data

    async def get_connector(_server):
        return Connector()

    async def unexpected_fan_control(*_args):
        raise AssertionError("fan control must not run for a powered-off host")

    monkeypatch.setattr(poller, "_get_connector", get_connector)
    monkeypatch.setattr(
        "dsm.sensor_poller.async_session", lambda: PersistingPollSessionContext(session)
    )
    monkeypatch.setattr(poller, "_maybe_auto_control_fans", unexpected_fan_control)

    result = await poller.poll_server(server)

    assert result == {
        "status": "error",
        "server": "R720XD",
        "server_id": 2,
        "error": "Server is powered off",
    }
    assert server.status == "degraded"
    assert server.last_seen == last_seen
    assert session.readings == []
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_profile", "expected_profile"),
    [("idrac8", "idrac8"), ("unknown", "idrac7")],
)
async def test_poll_server_preserves_explicit_control_profile_and_detects_unknown(
    monkeypatch, stored_profile, expected_profile
):
    poller = SensorPoller()
    server = types.SimpleNamespace(
        id=3,
        name="R720XD",
        drac_version=stored_profile,
        status="unknown",
        model=None,
        serial=None,
    )
    session = PersistingPollSession(server)
    sensor_data = types.SimpleNamespace(
        system_info=types.SimpleNamespace(
            model="PowerEdge R720XD",
            service_tag="service-tag",
            drac_version="idrac7",
            power_state="On",
        ),
        temperatures=[],
        fans=[],
    )

    class Connector:
        async def get_sensors(self):
            return sensor_data

    async def get_connector(_server):
        return Connector()

    async def no_fan_control(*_args):
        return None

    monkeypatch.setattr(poller, "_get_connector", get_connector)
    monkeypatch.setattr(
        "dsm.sensor_poller.async_session", lambda: PersistingPollSessionContext(session)
    )
    monkeypatch.setattr(poller, "_maybe_auto_control_fans", no_fan_control)

    result = await poller.poll_server(server)

    assert result["status"] == "ok"
    assert server.drac_version == expected_profile


@pytest.mark.asyncio
async def test_poll_all_caches_normalized_snapshot_and_broadcasts_versioned_event(monkeypatch):
    poller = SensorPoller()
    server = types.SimpleNamespace(id=1, name="R730xd")
    monkeypatch.setattr("dsm.sensor_poller.async_session", lambda: PollServerSessionContext([server]))

    async def poll_one(_server):
        return {
            "status": "ok", "server": "R730xd", "server_id": 1,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "temperatures": [{"name": "CPU1 Temp", "type": "cpu", "value": 52.5}],
            "fans": [{"name": "Fan 1", "member_id": "Fan1", "rpm": 5400, "percent": 27,
                      "percent_source": "pwm", "health": "OK"}],
        }

    events = []
    async def record_event(event):
        events.append(event)

    monkeypatch.setattr(poller, "poll_server", poll_one)
    monkeypatch.setattr(poller, "_broadcast", record_event)

    await poller.poll_all()

    snapshot = poller.get_cached_snapshot(1)
    assert snapshot["revision"] == 1
    assert snapshot["freshness"]["source"] == "memory"
    assert snapshot["readings"] == [{"label": "CPU1 Temp", "type": "cpu", "value": 52.5,
                                      "timestamp": "2026-01-01T00:00:00+00:00"}]
    assert snapshot["fans"][0]["percent_source"] == "pwm"
    assert events[0]["event"] == "server.telemetry.updated"
    assert events[0]["cycle_id"] == snapshot["cycle_id"]
    assert events[0]["revision"] == snapshot["revision"]
    assert events[0]["data"]["server_id"] == snapshot["server_id"]
    assert events[0]["data"]["readings"] == snapshot["readings"]
    assert events[0]["data"]["fans"] == snapshot["fans"]
    assert events[0]["data"]["freshness"]["captured_at"] == snapshot["freshness"]["captured_at"]
    assert events[1]["event"] == "poll.cycle.completed"
    assert events[1]["data"]["cycle_id"] == snapshot["cycle_id"]


@pytest.mark.asyncio
async def test_failed_poll_preserves_last_good_normalized_snapshot(monkeypatch):
    poller = SensorPoller()
    server = types.SimpleNamespace(id=1, name="R730xd")
    monkeypatch.setattr("dsm.sensor_poller.async_session", lambda: PollServerSessionContext([server]))
    outcomes = [
        {"status": "ok", "server": "R730xd", "server_id": 1,
         "timestamp": "2026-01-01T00:00:00+00:00",
         "temperatures": [{"name": "CPU1 Temp", "type": "cpu", "value": 52}], "fans": []},
        {"status": "error", "server": "R730xd", "server_id": 1, "error": "iDRAC timed out"},
    ]

    async def poll_one(_server):
        return outcomes.pop(0)

    monkeypatch.setattr(poller, "poll_server", poll_one)
    await poller.poll_all()
    await poller.poll_all()

    snapshot = poller.get_cached_snapshot(1)
    assert snapshot["revision"] == 2
    assert snapshot["readings"][0]["value"] == 52
    assert snapshot["freshness"]["stale"] is True
    assert snapshot["freshness"]["last_error"] == "iDRAC timed out"


@pytest.mark.asyncio
async def test_poll_all_bounds_concurrency_and_serializes_cycles(monkeypatch):
    poller = SensorPoller()
    servers = [types.SimpleNamespace(id=index, name=f"server-{index}") for index in range(1, 5)]
    monkeypatch.setattr("dsm.sensor_poller.async_session", lambda: PollServerSessionContext(servers))
    monkeypatch.setattr("dsm.sensor_poller.settings.sensor_poll_concurrency", 2)
    active = 0
    peak = 0

    async def poll_one(server):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"status": "ok", "server": server.name, "server_id": server.id,
                "timestamp": "2026-01-01T00:00:00+00:00", "temperatures": [], "fans": []}

    monkeypatch.setattr(poller, "poll_server", poll_one)
    await asyncio.gather(poller.poll_all(), poller.poll_all())

    assert peak == 2
    assert poller.get_dashboard_snapshot()["poll_cycle"]["cycle_id"] == 2


@pytest.mark.asyncio
async def test_poll_all_cancellation_cleans_up_tasks_and_skips_completed_event(monkeypatch):
    poller = SensorPoller()
    servers = [types.SimpleNamespace(id=index, name=f"server-{index}") for index in range(1, 3)]
    monkeypatch.setattr("dsm.sensor_poller.async_session", lambda: PollServerSessionContext(servers))
    monkeypatch.setattr("dsm.sensor_poller.settings.sensor_poll_concurrency", 2)
    started = asyncio.Event()
    cancelled = []
    active = 0
    events = []

    async def poll_one(_server):
        nonlocal active
        active += 1
        if active == len(servers):
            started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(_server.id)
            raise

    async def record_event(event):
        events.append(event)

    monkeypatch.setattr(poller, "poll_server", poll_one)
    monkeypatch.setattr(poller, "_broadcast", record_event)

    cycle = asyncio.create_task(poller.poll_all())
    await started.wait()
    cycle.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cycle

    assert sorted(cancelled) == [1, 2]
    assert poller.get_dashboard_snapshot()["poll_cycle"]["status"] == "cancelled"
    assert not any(event["event"] == "poll.cycle.completed" for event in events)


@pytest.mark.asyncio
async def test_poll_loop_uses_monotonic_max_interval_or_duration_cadence(monkeypatch):
    clock = FakeMonotonicClock()
    poller = SensorPoller(monotonic_clock=clock)
    poller._running = True
    sleeps = []

    async def poll_once():
        clock.value += 5  # longer than the configured interval

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        poller._running = False

    monkeypatch.setattr(poller, "poll_all", poll_once)
    monkeypatch.setattr("dsm.sensor_poller.settings.sensor_poll_interval", 3)
    monkeypatch.setattr("dsm.sensor_poller.asyncio.sleep", fake_sleep)

    await poller._poll_loop()

    assert sleeps == [0.0]


@pytest.mark.asyncio
async def test_snapshot_events_only_reach_the_subscribed_server():
    poller = SensorPoller()

    class Socket:
        def __init__(self):
            self.messages = []

        async def send_text(self, message):
            self.messages.append(message)

    matching = Socket()
    other = Socket()
    dashboard = Socket()
    poller.add_websocket_client(matching, server_id=1)
    poller.add_websocket_client(other, server_id=2)
    poller.add_dashboard_websocket_client(dashboard)

    await poller._broadcast_snapshot(
        {"server_id": 1, "revision": 4, "freshness": {}, "readings": [], "fans": []}, cycle_id=9
    )

    assert len(matching.messages) == 1
    assert other.messages == []
    assert len(dashboard.messages) == 1

    await poller._broadcast({"event": "poll.cycle.completed", "cycle_id": 9, "data": {}})
    assert len(matching.messages) == 1
    assert other.messages == []
    assert len(dashboard.messages) == 2


@pytest.mark.asyncio
async def test_broadcast_deduplicates_recipients_and_removes_dead_client_from_all_streams():
    poller = SensorPoller()

    class DeadSocket:
        def __init__(self):
            self.send_attempts = 0

        async def send_text(self, _message):
            self.send_attempts += 1
            raise RuntimeError("disconnected")

    dead = DeadSocket()
    poller.add_dashboard_websocket_client(dead)
    poller.add_websocket_client(dead, server_id=1)

    await poller._broadcast_snapshot(
        {"server_id": 1, "revision": 4, "freshness": {}, "readings": [], "fans": []}, cycle_id=9
    )

    assert dead.send_attempts == 1
    assert poller._dashboard_websocket_clients == []
    assert poller._server_websocket_clients == {}


@pytest.mark.asyncio
async def test_auto_control_uses_successful_command_target_not_stale_observed_pwm(monkeypatch):
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

    # The prior command was 14%, but PWM telemetry has not yet caught up.  The
    # command baseline must not be overwritten by this stale observation.
    poller._last_fan_control_target[1] = 14

    result = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, sensor_data)

    assert result["current_fan_percent"] == 14
    assert result["target_fan_percent"] == 11
    assert poller._last_fan_control_target[1] == 11
    assert poller._last_observed_fan_percent[1] == 22
    assert connector.calls == [("Manual", 11)]


@pytest.mark.asyncio
async def test_hot_auto_control_never_commands_below_higher_observed_pwm(monkeypatch):
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
    assert result["target_fan_percent"] >= 27
    assert connector.calls == [("Manual", result["target_fan_percent"])]


@pytest.mark.asyncio
async def test_auto_control_keeps_ipmi_keepalive_target_when_observed_pwm_is_stale(monkeypatch):
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
    assert poller._last_fan_control_target[1] == 14
    assert poller._last_observed_fan_percent[1] == 22

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

    assert connector.calls == [("Manual", 14)]


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
    clock = FakeMonotonicClock()
    poller = SensorPoller(monotonic_clock=clock)
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
    clock.value = settings.fan_control_idrac7_refresh_interval_seconds - 1
    await poller._refresh_auto_control_targets_once()
    clock.value += 1
    await poller._refresh_auto_control_targets_once()

    assert connector.calls == [('Manual', 7), ('Manual', 7)]


@pytest.mark.asyncio
async def test_auto_keepalive_seeds_observed_pwm_after_restart_without_churn(monkeypatch):
    poller = SensorPoller()
    connector = FakeConnector()
    connector.drac_version = "idrac7"
    # Simulates the fresh poller state after restart, populated by its first
    # successful sensor poll before the keepalive loop runs.
    poller._last_observed_fan_percent[1] = 27

    server = types.SimpleNamespace(id=1, name="R730xd")
    config = types.SimpleNamespace(auto_control=True, mode="auto")

    class FakeSessionForRows:
        async def execute(self, query):
            return types.SimpleNamespace(all=lambda: [(server, config)])

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
    await poller._refresh_auto_control_targets_once()

    assert poller._last_fan_control_target[1] == 27
    assert connector.calls == [("Manual", 27)]


def test_temperature_rate_is_calculated_per_server_sensor_from_monotonic_time():
    clock = FakeMonotonicClock()
    poller = SensorPoller(monotonic_clock=clock)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sensor = TempSensor(name="CPU1 Temp", value_celsius=50.0, physical_context="CPU")

    assert poller._temperature_rates(1, [sensor], start) == {}

    warmer = TempSensor(name="CPU1 Temp", value_celsius=54.0, physical_context="CPU")
    clock.value = 8
    rates = poller._temperature_rates(1, [warmer], start + timedelta(seconds=8))

    assert rates == {"cpu1 temp|cpu": 0.5}


def test_temperature_rates_reject_non_positive_intervals_and_long_gaps():
    clock = FakeMonotonicClock()
    poller = SensorPoller(monotonic_clock=clock)
    sensor = TempSensor(name="CPU1 Temp", value_celsius=50.0, physical_context="CPU")

    assert poller._temperature_rates(1, [sensor]) == {}

    assert poller._temperature_rates(1, [TempSensor(name="CPU1 Temp", value_celsius=55.0, physical_context="CPU")]) == {}

    clock.value = settings.fan_control_max_temperature_sample_gap_seconds + 1
    assert poller._temperature_rates(1, [TempSensor(name="CPU1 Temp", value_celsius=60.0, physical_context="CPU")]) == {}

    clock.value += 2
    assert poller._temperature_rates(1, [TempSensor(name="CPU1 Temp", value_celsius=64.0, physical_context="CPU")]) == {
        "cpu1 temp|cpu": 2.0
    }


@pytest.mark.asyncio
async def test_rapid_cpu_rise_near_max_gets_fast_bounded_response(monkeypatch):
    clock = FakeMonotonicClock()
    poller = SensorPoller(monotonic_clock=clock)
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
    clock.value = 2
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
    clock = FakeMonotonicClock()
    poller = SensorPoller(monotonic_clock=clock)
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
    clock.value = 5
    result = await poller._maybe_auto_control_fans(
        # The wall clock can jump arbitrarily; the five monotonic seconds are
        # what keep the dwell active.
        FakeSession(fan_config), server, connector, sensor_data, now=start + timedelta(hours=1)
    )

    assert result["action_taken"] == "unchanged"
    assert "Minimum automatic command interval" in result["reason"]
    assert connector.calls == [("Manual", 39)]


@pytest.mark.asyncio
async def test_heating_during_dwell_uses_last_command_not_stale_pwm(monkeypatch):
    clock = FakeMonotonicClock()
    poller = SensorPoller(monotonic_clock=clock)
    connector = FakeConnector()
    server = types.SimpleNamespace(id=1)
    fan_config = types.SimpleNamespace(auto_control=True, cpu_temp_min=35.0, cpu_temp_max=70.0,
                                       disk_temp_min=32.0, disk_temp_max=45.0)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def profile_ranges(*_args):
        return []

    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", profile_ranges)
    initial = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=75.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=20, health="OK")],
    )
    hotter_stale_pwm = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=79.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=20, health="OK")],
    )

    first = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, initial, now=start)
    clock.value = 5
    during_dwell = await poller._maybe_auto_control_fans(
        FakeSession(fan_config), server, connector, hotter_stale_pwm, now=start + timedelta(seconds=5)
    )
    clock.value = settings.fan_control_min_command_interval_seconds + 1
    after_dwell = await poller._maybe_auto_control_fans(
        FakeSession(fan_config), server, connector, hotter_stale_pwm,
        now=start + timedelta(seconds=settings.fan_control_min_command_interval_seconds + 1),
    )

    assert first["target_fan_percent"] == 32
    assert during_dwell["current_fan_percent"] == 32
    assert during_dwell["target_fan_percent"] == 32
    assert after_dwell["current_fan_percent"] == 32
    assert after_dwell["target_fan_percent"] == 44
    assert connector.calls == [("Manual", 32), ("Manual", 44)]


def test_emergency_overtemperature_boundary_bypasses_dwell():
    controller = FanController(FakeConnector(), cpu_temp_max=70.0)
    sensor = TempSensor(
        name="CPU1 Temp",
        value_celsius=70.0 + settings.fan_control_emergency_cpu_overtemp_c,
        physical_context="CPU",
    )

    assert SensorPoller._is_emergency_cpu_rise(controller, [sensor], [], {}) is True


@pytest.mark.asyncio
async def test_emergency_cpu_rise_bypasses_command_dwell(monkeypatch):
    clock = FakeMonotonicClock()
    poller = SensorPoller(monotonic_clock=clock)
    connector = FakeConnector()
    server = types.SimpleNamespace(id=1)
    fan_config = types.SimpleNamespace(auto_control=True, cpu_temp_min=35.0, cpu_temp_max=70.0,
                                       disk_temp_min=32.0, disk_temp_max=45.0)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def profile_ranges(*_args):
        return []

    monkeypatch.setattr("dsm.sensor_poller.get_active_temp_profile_ranges", profile_ranges)
    initial = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=75.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=20, health="OK")],
    )
    rapidly_rising = types.SimpleNamespace(
        temperatures=[TempSensor(name="CPU1 Temp", value_celsius=77.0, physical_context="CPU")],
        fans=[FanSensor(name="Fan 1", rpm=6000, member_id="Fan1", percent=20, health="OK")],
    )

    first = await poller._maybe_auto_control_fans(FakeSession(fan_config), server, connector, initial, now=start)
    clock.value = 1
    emergency = await poller._maybe_auto_control_fans(
        FakeSession(fan_config), server, connector, rapidly_rising, now=start + timedelta(seconds=5)
    )

    assert first["target_fan_percent"] == 32
    assert emergency["target_fan_percent"] == 44
    assert connector.calls == [("Manual", 32), ("Manual", 44)]
