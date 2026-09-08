"""Regression tests for the MCP 1.x streamable-HTTP integration."""

from unittest.mock import Mock

from dsm.mcp import server


def test_fastmcp_exposes_the_documented_streamable_http_endpoint():
    app = server.mcp.streamable_http_app()

    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)


def test_mcp_entrypoint_uses_streamable_http(monkeypatch):
    run = Mock()
    monkeypatch.setattr(server.mcp, "run", run)

    server.main()

    run.assert_called_once_with("streamable-http")
