from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from dsm.api import idrac as idrac_api
from dsm.idrac_connector import IdracConnectionError, IdracError
from dsm.temp_profile_repository import get_active_temp_profile_ranges


class FakeConnector:
    def __init__(self, **_kwargs):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_execute_idrac_request_closes_connector_and_preserves_connection_message(monkeypatch):
    created = []

    def make_connector(**kwargs):
        connector = FakeConnector(**kwargs)
        created.append(connector)
        return connector

    monkeypatch.setattr(idrac_api, "decrypt_ciphertext", lambda _: "plaintext")
    monkeypatch.setattr(idrac_api, "IdracConnector", make_connector)

    async def fail(_connector):
        raise IdracConnectionError("network unavailable")

    with pytest.raises(HTTPException) as error:
        await idrac_api.execute_idrac_request(
            SimpleNamespace(ipmi_password_enc="encrypted", ipmi_ip="10.0.0.1", ipmi_user="user", drac_version="idrac8"),
            fail,
            credential_error_detail="Cannot decrypt credentials",
            connection_error_detail=lambda exc: f"Cannot connect to iDRAC: {exc}",
        )

    assert error.value.status_code == 502
    assert error.value.detail == "Cannot connect to iDRAC: network unavailable"
    assert created[0].closed is True


@pytest.mark.asyncio
async def test_execute_idrac_request_preserves_idrac_error_message_and_closes(monkeypatch):
    created = []

    def make_connector(**kwargs):
        connector = FakeConnector(**kwargs)
        created.append(connector)
        return connector

    monkeypatch.setattr(idrac_api, "decrypt_ciphertext", lambda _: "plaintext")
    monkeypatch.setattr(idrac_api, "IdracConnector", make_connector)

    async def fail(_connector):
        raise IdracError("sensor query failed")

    with pytest.raises(HTTPException) as error:
        await idrac_api.execute_idrac_request(
            SimpleNamespace(ipmi_password_enc="encrypted", ipmi_ip="10.0.0.1", ipmi_user="user", drac_version="idrac8"),
            fail,
            credential_error_detail="Cannot decrypt credentials",
            idrac_error_detail=lambda exc: f"iDRAC sensor query failed: {exc}",
        )

    assert error.value.status_code == 502
    assert error.value.detail == "iDRAC sensor query failed: sensor query failed"
    assert created[0].closed is True


@pytest.mark.asyncio
async def test_active_temp_profile_ranges_returns_only_default_profile_ranges():
    profile = SimpleNamespace(id=7)
    ranges = [SimpleNamespace(profile_id=7), SimpleNamespace(profile_id=7)]

    class Result:
        def __init__(self, value):
            self.value = value

        def scalars(self):
            return self

        def first(self):
            return self.value

        def all(self):
            return self.value

    class Session:
        def __init__(self):
            self.results = [Result(profile), Result(ranges)]

        async def execute(self, _query):
            return self.results.pop(0)

    assert await get_active_temp_profile_ranges(Session(), 1) == ranges


@pytest.mark.asyncio
async def test_active_temp_profile_ranges_returns_empty_without_a_default_profile():
    class Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class Session:
        async def execute(self, _query):
            return Result()

    assert await get_active_temp_profile_ranges(Session(), 1) == []
