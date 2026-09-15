"""Focused cache-first dashboard REST and WebSocket regression coverage."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from dsm.api import sensors as sensors_api
from dsm.sensor_poller import SensorPoller


class ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class SnapshotSession:
    def __init__(self, servers, readings):
        self.servers = servers
        self.readings = readings
        self.calls = 0
        self.queries = []

    async def execute(self, query):
        self.calls += 1
        self.queries.append(query)
        return ScalarResult(self.servers if self.calls == 1 else self.readings)


@pytest.mark.asyncio
async def test_dashboard_snapshot_prefers_cache_and_never_contacts_idrac(monkeypatch):
    poller = SensorPoller()
    poller._snapshot_cache[1] = {
        "server_id": 1,
        "server_name": "cached",
        "status": "online",
        "revision": 4,
        "cycle_id": 3,
        "freshness": {
            "source": "memory", "captured_at": datetime.now(timezone.utc).isoformat(),
            "age_seconds": 0, "stale": False, "last_error": None, "last_attempt_at": None,
        },
        "readings": [{"label": "CPU1", "type": "cpu", "value": 51, "timestamp": None}],
        "fans": [],
    }
    session = SnapshotSession([SimpleNamespace(id=1, name="cached", status="online")], [])

    async def unexpected_idrac(*_args, **_kwargs):
        raise AssertionError("dashboard snapshot must not contact iDRAC")

    monkeypatch.setattr(sensors_api, "poller", poller)
    monkeypatch.setattr(sensors_api, "execute_idrac_request", unexpected_idrac)

    payload = await sensors_api.get_dashboard_snapshot(_user=object(), session=session)

    assert payload["servers"][0]["freshness"]["source"] == "memory"
    assert payload["servers"][0]["revision"] == 4
    assert session.calls == 1


@pytest.mark.asyncio
async def test_dashboard_snapshot_uses_latest_database_readings_on_cold_start(monkeypatch):
    poller = SensorPoller()
    timestamp = datetime.now(timezone.utc)
    server = SimpleNamespace(id=7, name="cold-start", status="offline")
    readings = [
        SimpleNamespace(sensor_label="CPU1", sensor_type="cpu", value=49, timestamp=timestamp),
        SimpleNamespace(sensor_label="CPU1", sensor_type="cpu", value=45, timestamp=timestamp.replace(year=2025)),
        SimpleNamespace(sensor_label="Inlet", sensor_type="ambient", value=21, timestamp=timestamp),
    ]
    session = SnapshotSession([server], readings)
    monkeypatch.setattr(sensors_api, "poller", poller)

    payload = await sensors_api.get_dashboard_snapshot(_user=object(), session=session)
    snapshot = payload["servers"][0]

    assert snapshot["freshness"]["source"] == "database"
    assert snapshot["revision"] == 0
    assert snapshot["cycle_id"] == 0
    assert snapshot["freshness"]["captured_at"] == timestamp.isoformat()
    assert snapshot["freshness"]["age_seconds"] is not None
    assert {item["label"] for item in snapshot["readings"]} == {"CPU1", "Inlet"}
    assert next(item for item in snapshot["readings"] if item["label"] == "CPU1")["value"] == 49
    query_sql = str(session.queries[1]).upper()
    assert "GROUP BY" in query_sql
    assert "MAX(" in query_sql


class UserSessionContext:
    def __init__(self, user):
        self.user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _model, _user_id):
        return self.user


class FakeWebSocket:
    def __init__(self, token=None):
        self.query_params = {} if token is None else {"token": token}
        self.headers = {}
        self.accepted = False
        self.close_code = None

    async def accept(self):
        self.accepted = True

    async def close(self, code):
        self.close_code = code

    async def receive_text(self):
        raise WebSocketDisconnect(code=1000)


@pytest.mark.asyncio
async def test_dashboard_websocket_requires_jwt_and_cleans_up_client(monkeypatch):
    poller = SensorPoller()
    monkeypatch.setattr(sensors_api, "poller", poller)

    denied = FakeWebSocket()
    await sensors_api.dashboard_websocket(denied)
    assert denied.accepted is False
    assert denied.close_code == 1008

    accepted = FakeWebSocket("valid-token")
    monkeypatch.setattr(sensors_api, "decode_token", lambda token: {"sub": "42"})
    monkeypatch.setattr(
        sensors_api,
        "async_session",
        lambda: UserSessionContext(SimpleNamespace(is_active=True)),
    )
    await sensors_api.dashboard_websocket(accepted)

    assert accepted.accepted is True
    assert poller._dashboard_websocket_clients == []
