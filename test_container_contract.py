"""Regression checks for the Docker deployment contract without requiring Docker."""

from pathlib import Path


ROOT = Path(__file__).parent


def test_dockerfile_builds_and_serves_frontend_as_non_root_user():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "FROM node:22-bookworm-slim AS frontend-build" in dockerfile
    assert "RUN cd frontend && npm ci" in dockerfile
    assert "COPY frontend/package-lock.json ./frontend/package-lock.json" in dockerfile
    assert "PYTHONPATH=/app/src" in dockerfile
    assert "COPY --from=frontend-build /app/src/dsm/frontend ./src/dsm/frontend" in dockerfile
    assert "USER dsm" in dockerfile
    assert "DSM_REQUIRE_BOOTSTRAP_ADMIN=true" in dockerfile
    assert 'CMD ["uvicorn", "dsm.app:app", "--host", "0.0.0.0", "--port", "8080"]' in dockerfile


def test_compose_persists_sqlite_and_keeps_application_and_mcp_ports_on_loopback():
    compose = (ROOT / "compose.yaml").read_text()

    assert "DSM_DB_PATH: /var/lib/dsm/dsm.db" in compose
    assert "- dsm_data:/var/lib/dsm" in compose
    assert '"127.0.0.1:8080:8080"' in compose
    assert '"127.0.0.1:8101:8101"' in compose
    assert "DSM_ENCRYPTION_KEY: ${DSM_ENCRYPTION_KEY:?DSM_ENCRYPTION_KEY must be set in .env}" in compose
    assert 'DSM_REQUIRE_BOOTSTRAP_ADMIN: "true"' in compose
    assert "DSM_BOOTSTRAP_ADMIN_PASSWORD: ${DSM_BOOTSTRAP_ADMIN_PASSWORD:?DSM_BOOTSTRAP_ADMIN_PASSWORD must be set in .env}" in compose
    assert "healthcheck:" in compose
    assert "/health" in compose


def test_example_environment_contains_only_a_placeholder_encryption_key():
    example = (ROOT / ".env.example").read_text()

    assert "DSM_ENCRYPTION_KEY=replace_with_" in example
    assert "DSM_BOOTSTRAP_ADMIN_PASSWORD=replace_with_" in example
    assert "00000000000000000000000000000000" not in example


def test_docker_development_contract_uses_wrappers_and_keeps_lan_override_untracked():
    makefile = (ROOT / "Makefile").read_text()
    build_script = (ROOT / "scripts" / "docker-build.sh").read_text()
    deploy_script = (ROOT / "scripts" / "docker-deploy.sh").read_text()
    gitignore = (ROOT / ".gitignore").read_text()
    lan_override = (ROOT / "compose.lan.yaml.example").read_text()

    for target in ("test", "docker-build", "docker-config", "docker-up", "docker-down", "docker-logs"):
        assert f"{target}:" in makefile
    assert "./scripts/docker-build.sh" in makefile
    assert "./scripts/docker-deploy.sh" in makefile
    assert "docker compose config" in makefile
    assert "docker build --file Dockerfile" in build_script
    assert "DSM_ENCRYPTION_KEY" in deploy_script
    assert "DSM_BOOTSTRAP_ADMIN_PASSWORD" in deploy_script
    assert "docker compose up --build --detach" in deploy_script
    assert "compose.override.yaml" in gitignore
    assert "ports: !override" in lan_override
    assert '"0.0.0.0:8080:8080"' in lan_override
