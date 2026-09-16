"""Regression checks for the Docker deployment contract without requiring Docker."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


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


def test_docker_deploy_rejects_invalid_encryption_keys_without_invoking_compose(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\ntouch \"$FAKE_DOCKER_CALLED\"\n")
    fake_docker.chmod(0o755)
    called = tmp_path / "docker-called"

    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_CALLED": str(called),
        "DSM_ENCRYPTION_KEY": "not-a-valid-key",
        "DSM_BOOTSTRAP_ADMIN_PASSWORD": "test-password",
    }
    result = subprocess.run(
        [str(ROOT / "scripts" / "docker-deploy.sh")],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "exactly 32 hexadecimal characters" in result.stderr
    assert "not-a-valid-key" not in result.stderr
    assert not called.exists()


def test_docker_deploy_accepts_exactly_32_hexadecimal_characters(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$FAKE_DOCKER_ARGS\"\n")
    fake_docker.chmod(0o755)
    arguments = tmp_path / "docker-arguments"

    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_ARGS": str(arguments),
        "DSM_ENCRYPTION_KEY": "A1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6",
        "DSM_BOOTSTRAP_ADMIN_PASSWORD": "test-password",
    }
    result = subprocess.run(
        [str(ROOT / "scripts" / "docker-deploy.sh"), "--force-recreate"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert arguments.read_text().strip() == "compose up --build --detach --force-recreate"


def test_docker_deploy_does_not_source_dotenv_files():
    deploy_script = (ROOT / "scripts" / "docker-deploy.sh").read_text()

    assert "source .env" not in deploy_script
    assert "dotenv_value" in deploy_script


@pytest.mark.parametrize(
    ("password_line", "expected_password"),
    [
        ('"password # inside double quotes" # password comment', "password # inside double quotes"),
        ("'password # inside single quotes' # password comment", "password # inside single quotes"),
    ],
)
def test_docker_deploy_matches_compose_dotenv_comment_rules_for_required_secrets(
    tmp_path, password_line, expected_password
):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "docker-deploy.sh", scripts / "docker-deploy.sh")
    (project / ".env").write_text(
        "DSM_ENCRYPTION_KEY=A1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6 # key comment\n"
        f"DSM_BOOTSTRAP_ADMIN_PASSWORD={password_line}\n"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n%s\\n" "$DSM_ENCRYPTION_KEY" "$DSM_BOOTSTRAP_ADMIN_PASSWORD" '
        '> "$FAKE_DOCKER_VALUES"\n'
    )
    fake_docker.chmod(0o755)
    values = tmp_path / "docker-values"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_VALUES": str(values),
    }
    environment.pop("DSM_ENCRYPTION_KEY", None)
    environment.pop("DSM_BOOTSTRAP_ADMIN_PASSWORD", None)

    result = subprocess.run(
        [str(scripts / "docker-deploy.sh")],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert values.read_text().splitlines() == [
        "A1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6",
        expected_password,
    ]


def test_docker_deploy_rejects_unsupported_dotenv_secret_forms_before_compose(tmp_path):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "docker-deploy.sh", scripts / "docker-deploy.sh")
    (project / ".env").write_text(
        'DSM_ENCRYPTION_KEY="A1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6" unexpected\n'
        "DSM_BOOTSTRAP_ADMIN_PASSWORD=test-password\n"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text('#!/bin/sh\ntouch "$FAKE_DOCKER_CALLED"\n')
    fake_docker.chmod(0o755)
    called = tmp_path / "docker-called"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_CALLED": str(called),
    }
    environment.pop("DSM_ENCRYPTION_KEY", None)
    environment.pop("DSM_BOOTSTRAP_ADMIN_PASSWORD", None)

    result = subprocess.run(
        [str(scripts / "docker-deploy.sh")],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "DSM_ENCRYPTION_KEY in .env uses an unsupported dotenv form." in result.stderr
    assert "A1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6" not in result.stderr
    assert not called.exists()
