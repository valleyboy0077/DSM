"""Regression checks for the Docker deployment contract without requiring Docker."""

from pathlib import Path


ROOT = Path(__file__).parent


def test_dockerfile_builds_and_serves_frontend_as_non_root_user():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "FROM node:22-bookworm-slim AS frontend-build" in dockerfile
    assert "npm run build" in dockerfile
    assert "PYTHONPATH=/app/src" in dockerfile
    assert "COPY --from=frontend-build /app/src/dsm/frontend ./src/dsm/frontend" in dockerfile
    assert "USER dsm" in dockerfile
    assert 'CMD ["uvicorn", "dsm.app:app", "--host", "0.0.0.0", "--port", "8080"]' in dockerfile


def test_compose_persists_sqlite_and_exposes_application_and_mcp_ports():
    compose = (ROOT / "compose.yaml").read_text()

    assert "DSM_DB_PATH: /var/lib/dsm/dsm.db" in compose
    assert "- dsm_data:/var/lib/dsm" in compose
    assert '"8080:8080"' in compose
    assert '"8101:8101"' in compose
    assert "DSM_ENCRYPTION_KEY: ${DSM_ENCRYPTION_KEY:?DSM_ENCRYPTION_KEY must be set in .env}" in compose
    assert "healthcheck:" in compose
    assert "/health" in compose


def test_example_environment_contains_only_a_placeholder_encryption_key():
    example = (ROOT / ".env.example").read_text()

    assert "DSM_ENCRYPTION_KEY=replace_with_" in example
    assert "00000000000000000000000000000000" not in example
