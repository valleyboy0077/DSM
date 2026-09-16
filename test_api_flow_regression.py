from test_api_flow import create_server, delete_created_server


class FailedCreateClient:
    def __init__(self):
        self.deleted_paths = []

    def post(self, *_args, **_kwargs):
        return type("Response", (), {"status_code": 500})()

    def delete(self, path, **_kwargs):
        self.deleted_paths.append(path)


def test_failed_creation_cannot_delete_fallback_server_id_one():
    client = FailedCreateClient()

    created = create_server(client, {"name": "R730xd-Test"}, {"Authorization": "Bearer test"})
    response = delete_created_server(client, created, {"Authorization": "Bearer test"})

    assert created is None
    assert response is None
    assert client.deleted_paths == []
