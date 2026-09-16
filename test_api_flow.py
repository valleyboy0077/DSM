"""Side-effect safety helpers for the API smoke test."""
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class CreatedServer:
    """A server ID that was returned by this test run's successful create."""

    id: int


def create_server(client: Any, payload: Mapping[str, Any], headers: Mapping[str, str]) -> Optional[CreatedServer]:
    """Create a server, returning an ownership token only on a valid success."""
    response = client.post("/servers/", json=dict(payload), headers=headers)
    if response.status_code != 201:
        return None

    try:
        server_id = response.json().get("id")
    except (TypeError, ValueError):
        return None
    if isinstance(server_id, bool) or not isinstance(server_id, int):
        return None
    return CreatedServer(id=server_id)


def delete_created_server(client: Any, created: Optional[CreatedServer], headers: Mapping[str, str]):
    """Delete only a server this test run successfully created."""
    if created is None:
        return None
    return client.delete(f"/servers/{created.id}", headers=headers)
