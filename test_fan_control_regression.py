import json
import os
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from dsm.api import fans as fans_api
from dsm.fan_control import FanController
from dsm.idrac_connector import FanSensor, IdracConnector, IdracError, TempSensor


class FakeConnector:
    def __init__(self, drac_version='idrac8', racadm_success=True, ipmi_success=True):
        self.drac_version = drac_version
        self.ip = '10.0.0.1'
        self.calls = []
        self.racadm_success = racadm_success
        self.ipmi_success = ipmi_success

    async def set_fan_mode_redfish(self, mode, speed_percent=None):
        self.calls.append(("redfish", mode, speed_percent))
        self._fan_control_backend = "redfish"
        return True

    async def set_fan_mode_racadm(self, mode, speed_percent=None):
        self.calls.append(("racadm", mode, speed_percent))
        if self.racadm_success:
            self._fan_control_backend = "racadm"
        return self.racadm_success

    async def set_fan_mode_ipmi(self, mode, speed_percent=None):
        self.calls.append(("ipmi", mode, speed_percent))
        if self.ipmi_success:
            self._fan_control_backend = "ipmi"
        return self.ipmi_success


@pytest.mark.asyncio
async def test_ipmi_subprocess_requests_operator_privilege(monkeypatch, tmp_path):
    """The isolated pyghmi request must authenticate at operator privilege."""
    pyghmi_module = tmp_path / "pyghmi" / "ipmi"
    pyghmi_module.mkdir(parents=True)
    (tmp_path / "pyghmi" / "__init__.py").write_text("")
    (pyghmi_module / "__init__.py").write_text("")
    (pyghmi_module / "command.py").write_text(
        """
import json
import os


def _record(event):
    with open(os.environ["PYGHMI_TEST_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(event) + "\\n")


class _Session:
    def logout(self):
        _record({"event": "logout"})


class Command:
    def __init__(self, **kwargs):
        self.ipmi_session = _Session()
        _record({"event": "command", "privlevel": kwargs.get("privlevel")})

    def raw_command(self, **kwargs):
        _record({"event": "raw", "netfn": kwargs["netfn"], "command": kwargs["command"], "data": kwargs["data"]})
        return {"code": 0}
""".lstrip(),
        encoding="utf-8",
    )
    log_path = tmp_path / "pyghmi-events.jsonl"
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + (os.pathsep + existing_pythonpath if existing_pythonpath else ""))
    monkeypatch.setenv("PYGHMI_TEST_LOG", str(log_path))
    monkeypatch.setattr("dsm.idrac_connector.pyghmi_command", object())

    connector = IdracConnector(ip="127.0.0.1", username="operator", password="test-only")

    assert await connector.set_fan_mode_ipmi("Manual", 25) is True

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert events[0] == {"event": "command", "privlevel": 4}
    assert events[1:3] == [
        {"event": "raw", "netfn": 0x30, "command": 0x30, "data": [0x01, 0x00]},
        {"event": "raw", "netfn": 0x30, "command": 0x30, "data": [0x02, 0xFF, 25]},
    ]


@pytest.mark.asyncio
async def test_idrac7_prefers_ipmi_backend_for_new_servers():
    connector = FakeConnector(drac_version='idrac7')
    controller = FanController(connector=connector, fan_min=7)

    ok = await controller.set_manual_speed(25)

    assert ok is True
    assert connector.calls == [('ipmi', 'Manual', 25)]
    assert getattr(connector, '_fan_control_backend', None) == 'ipmi'


@pytest.mark.asyncio
async def test_idrac7_falls_back_to_racadm_when_ipmi_is_unauthorized():
    connector = FakeConnector(drac_version='idrac7', racadm_success=True, ipmi_success=False)
    controller = FanController(connector=connector, fan_min=7)

    ok = await controller.set_manual_speed(25)

    assert ok is True
    assert connector.calls == [('ipmi', 'Manual', 25), ('racadm', 'Manual', 25)]
    assert getattr(connector, '_fan_control_backend', None) == 'racadm'


@pytest.mark.asyncio
async def test_manual_override_preserves_requested_dell_duty_percent_on_idrac8():
    connector = FakeConnector(drac_version='idrac8')
    controller = FanController(connector=connector, fan_min=7)

    ok = await controller.set_manual_speed(37)

    assert ok is True
    assert connector.calls == [('redfish', 'Manual', 37)]


@pytest.mark.asyncio
async def test_manual_override_does_not_translate_a_valid_low_dell_duty_to_auto_floor():
    connector = FakeConnector(drac_version='idrac7')
    controller = FanController(connector=connector, fan_min=7)

    ok = await controller.set_manual_speed(1)

    assert ok is True
    assert connector.calls == [('ipmi', 'Manual', 1)]


@pytest.mark.asyncio
async def test_idrac7_unchanged_auto_cycle_refreshes_manual_target():
    connector = FakeConnector(drac_version='idrac7')
    controller = FanController(
        connector=connector,  # type: ignore[arg-type]
        cpu_temp_min=40.0,
        cpu_temp_max=50.0,
        disk_temp_min=32.0,
        disk_temp_max=45.0,
        fan_min=7,
        fan_max=100,
    )
    controller._mode = 'auto'

    sensor_data = SimpleNamespace(
        temperatures=[
            TempSensor(name='CPU1 Temp', value_celsius=38.0, physical_context='CPU'),
            TempSensor(name='System Board Inlet Temp', value_celsius=17.0, physical_context='SystemBoard'),
        ],
        fans=[FanSensor(name='Fan 1', rpm=3840, member_id='Fan1', percent=None, health='OK')],
    )

    result = await controller.control_cycle(
        sensor_data=sensor_data,
        current_fan_percent=7,
        step_percent=3,
    )

    assert result.action_taken == 'unchanged'
    assert result.target_fan_percent == 7
    assert connector.calls == [('ipmi', 'Manual', 7)]


@pytest.mark.asyncio
async def test_direct_auto_cycle_with_no_pwm_or_prior_target_does_not_command_fans():
    connector = FakeConnector(drac_version='idrac7')
    controller = FanController(connector=connector, fan_min=7)
    sensor_data = SimpleNamespace(
        temperatures=[TempSensor(name='CPU1 Temp', value_celsius=39.0, physical_context='CPU')],
        fans=[FanSensor(name='Fan 1', rpm=6000, member_id='Fan1', percent=50, percent_source='rpm_estimate')],
    )

    result = await controller.control_cycle(sensor_data=sensor_data)

    assert result.action_taken == 'no_action'
    assert 'No controller-reported fan duty' in result.reason
    assert connector.calls == []


@pytest.mark.asyncio
async def test_redfish_rpm_percentage_estimate_is_explicitly_marked_in_telemetry_payload(monkeypatch):
    connector = IdracConnector(ip='10.0.0.1', username='user', password='password')

    async def no_wsman_inventory(_class_name):
        raise IdracError('WS-Man unavailable')

    async def redfish_thermal(_path):
        return {
            'Fans': [{
                'Name': 'Fan 1', 'MemberId': 'Fan1', 'Reading': 6000,
                'MaxReadingRange': 12000, 'Status': {'Health': 'OK'},
            }],
        }

    monkeypatch.setattr(connector, '_wsman_enumerate', no_wsman_inventory)
    monkeypatch.setattr(connector, '_request', redfish_thermal)

    fans = await connector.get_fan_inventory()
    payload = fans_api.FanTelemetryItem(**fans[0]).model_dump()

    assert payload['percent'] == 50
    assert payload['percent_source'] == 'rpm_estimate'


@pytest.mark.asyncio
async def test_sensor_snapshot_prefers_wsman_pwm_for_automatic_control(monkeypatch):
    connector = IdracConnector(ip='10.0.0.1', username='user', password='password')

    async def redfish_thermal(_path):
        return {
            'Temperatures': [
                {'Name': 'CPU1 Temp', 'ReadingCelsius': 75, 'PhysicalContext': 'CPU'},
            ],
            'Fans': [
                {'Name': 'Fan 1', 'Reading': 3840, 'MemberId': 'Fan1'},
            ],
        }

    async def wsman_inventory():
        return [{
            'name': 'Fan 1',
            'member_id': 'Fan1',
            'rpm': 5760,
            'percent': 27,
            'percent_source': 'pwm',
            'health': 'OK',
            'source': 'wsman',
        }]

    async def detect_version():
        return None

    monkeypatch.setattr(connector, '_request', redfish_thermal)
    monkeypatch.setattr(connector, 'get_fan_inventory', wsman_inventory)
    monkeypatch.setattr(connector, 'detect_version', detect_version)

    snapshot = await connector.get_sensors()

    assert [(fan.rpm, fan.percent, fan.percent_source) for fan in snapshot.fans] == [(5760, 27, 'pwm')]


@pytest.mark.asyncio
async def test_malformed_wsman_fan_inventory_keeps_redfish_sensor_data_usable(monkeypatch):
    connector = IdracConnector(ip='10.0.0.1', username='user', password='password')

    async def malformed_wsman_inventory(_class_name):
        raise ET.ParseError('malformed WS-Man inventory')

    async def redfish_thermal(_path):
        return {
            'Temperatures': [{'Name': 'CPU1 Temp', 'ReadingCelsius': 75, 'PhysicalContext': 'CPU'}],
            'Fans': [{'Name': 'Fan 1', 'MemberId': 'Fan1', 'Reading': 5760, 'MaxReadingRange': 12000}],
        }

    async def detect_version():
        return None

    monkeypatch.setattr(connector, '_wsman_enumerate', malformed_wsman_inventory)
    monkeypatch.setattr(connector, '_request', redfish_thermal)
    monkeypatch.setattr(connector, 'detect_version', detect_version)

    snapshot = await connector.get_sensors()

    assert [temp.value_celsius for temp in snapshot.temperatures] == [75]
    assert [(fan.member_id, fan.rpm, fan.percent_source) for fan in snapshot.fans] == [
        ('Fan1', 5760, 'rpm_estimate'),
    ]


@pytest.mark.asyncio
async def test_incomplete_wsman_inventory_merges_with_redfish_by_fan_identity(monkeypatch):
    connector = IdracConnector(ip='10.0.0.1', username='user', password='password')

    async def wsman_inventory(_class_name):
        return [{
            'DeviceDescription': 'Fan 1',
            'FQDD': 'Fan.Embedded.1',
            'CurrentReading': '0',
            'PWM': '27',
            'PrimaryStatus': '1',
        }]

    async def redfish_thermal(_path):
        return {'Fans': [
            {'Name': 'Fan 1', 'MemberId': 'Fan1', 'Reading': 5760, 'MaxReadingRange': 12000},
            {'Name': 'Fan 2', 'MemberId': 'Fan2', 'Reading': 4800, 'MaxReadingRange': 12000},
        ]}

    monkeypatch.setattr(connector, '_wsman_enumerate', wsman_inventory)
    monkeypatch.setattr(connector, '_request', redfish_thermal)

    fans = await connector.get_fan_inventory()

    assert [(fan['member_id'], fan['rpm'], fan['percent'], fan['percent_source']) for fan in fans] == [
        ('Fan1', 5760, 27, 'pwm'),
        ('Fan2', 4800, 40, 'rpm_estimate'),
    ]


@pytest.mark.asyncio
async def test_incremental_fan_logic_holds_when_mixed_sensors_are_cool_and_in_range():
    connector = FakeConnector()
    controller = FanController(
        connector=connector,  # type: ignore[arg-type]
        cpu_temp_min=35.0,
        cpu_temp_max=70.0,
        disk_temp_min=32.0,
        disk_temp_max=45.0,
        fan_min=7,
        fan_max=100,
    )

    sensor_data = SimpleNamespace(
        temperatures=[
            TempSensor(name='CPU1 Temp', value_celsius=29.0, physical_context='CPU'),
            TempSensor(name='Disk Bay 1', value_celsius=38.0, physical_context='Drive'),
            TempSensor(name='Inlet', value_celsius=40.0, physical_context='SystemBoard'),
        ],
        fans=[FanSensor(name='Fan 1', rpm=6000, member_id='Fan1', percent=22, health='OK')],
    )

    result = await controller.control_cycle(
        sensor_data=sensor_data,
        current_fan_percent=22,
        step_percent=3,
    )

    assert result.action_taken == 'unchanged'
    assert result.target_fan_percent == 22
    assert 'within its configured range' in result.reason
    assert connector.calls == []


@pytest.mark.asyncio
async def test_run_cycle_does_not_treat_saved_manual_speed_as_live_pwm(monkeypatch):
    fake_server = SimpleNamespace(
        id=1,
        ipmi_ip='10.1.1.109',
        ipmi_user='ned',
        ipmi_password_enc='encrypted',
        drac_version='idrac7',
    )
    fake_config = SimpleNamespace(
        server_id=1,
        mode='auto',
        cpu_temp_min=40.0,
        cpu_temp_max=50.0,
        disk_temp_min=32.0,
        disk_temp_max=45.0,
        manual_speed=9,
        polling_seconds=20,
        auto_control=True,
    )

    class FakeResult:
        def __init__(self, obj):
            self._obj = obj

        def scalars(self):
            return self

        def first(self):
            return self._obj

    class FakeSession:
        async def get(self, model, pk):
            assert pk == 1
            return fake_server if model is fans_api.Server else None

        async def execute(self, query):
            return FakeResult(fake_config)

        async def commit(self):
            return None

        async def refresh(self, obj):
            return None

    created = []

    class FakeController:
        def __init__(self, connector, cpu_temp_min=45.0, cpu_temp_max=70.0, disk_temp_min=32.0, disk_temp_max=45.0):
            self.connector = connector
            self.calls = []
            created.append(self)

        async def control_cycle(self, sensor_data=None, current_fan_percent=None, profile_ranges=None, step_percent=3):
            self.calls.append(current_fan_percent)
            return SimpleNamespace(
                target_fan_percent=7,
                cpu_temp=39,
                disk_temp=None,
                ambient_temp=29,
                action_taken='decreased',
                reason='CPU1 Temp 39.0°C < 40.0°C min; decreasing fan by 3%',
            )

        async def set_manual_speed(self, speed):
            return True

        async def set_auto_mode(self):
            return True

        async def reset_to_default(self):
            return True

    async def fake_get_active_temp_profile_ranges(session, server_id):
        return []

    async def fake_execute_idrac_request(server, operation, **kwargs):
        return await operation(SimpleNamespace())

    monkeypatch.setattr(fans_api, 'FanController', FakeController)
    monkeypatch.setattr(fans_api, 'get_active_temp_profile_ranges', fake_get_active_temp_profile_ranges)
    monkeypatch.setattr(fans_api, 'execute_idrac_request', fake_execute_idrac_request)
    monkeypatch.setattr(fans_api, 'poller', SimpleNamespace(_last_fan_control_target={}, _last_fan_control_at={}), raising=False)

    result = await fans_api.control_fans(
        server_id=1,
        action=fans_api.FanControlAction(action='run_cycle'),
        _user=SimpleNamespace(),
        session=FakeSession(),
    )  # type: ignore[arg-type]

    assert result is not None
    assert result['fan_percent'] == 7
    assert created and created[0].calls == [None]
