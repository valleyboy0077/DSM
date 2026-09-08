"""Behavior contract for the packaged SPA built by the normal startup path."""

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parent


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _copy_startup_project(destination: Path) -> None:
    """Copy the startup/build inputs; frontend dependencies stay shared and read-only."""
    for filename in ("start.sh", "build.sh"):
        shutil.copy2(REPOSITORY / filename, destination / filename)
    shutil.copytree(
        REPOSITORY / "frontend",
        destination / "frontend",
        ignore=shutil.ignore_patterns("node_modules"),
    )
    (destination / "frontend" / "node_modules").symlink_to(
        REPOSITORY / "frontend" / "node_modules", target_is_directory=True
    )


def _startup_command_stubs(directory: Path) -> Path:
    """Provide a dependency-free Uvicorn stand-in after the real frontend build."""
    commands = directory / "bin"
    commands.mkdir()
    (commands / "python3").write_text(
        "#!/bin/sh\ntouch \"$DSM_TEST_UVICORN_MARKER\"\n", encoding="utf-8"
    )
    (commands / "curl").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for command in commands.iterdir():
        command.chmod(0o755)
    return commands


def test_startup_rebuilds_a_missing_frontend_bundle(tmp_path: Path):
    """Starting DSM replaces a stale SPA shell with a build whose JS asset exists."""
    project = tmp_path / "dsm"
    project.mkdir()
    _copy_startup_project(project)

    # This is a temporary generated artifact, deliberately invalid before startup.
    frontend_output = project / "src" / "dsm" / "frontend"
    frontend_output.mkdir(parents=True)
    (frontend_output / "index.html").write_text(
        '<script type="module" src="/assets/missing.js"></script>', encoding="utf-8"
    )

    port = _available_port()
    commands = _startup_command_stubs(tmp_path)
    server_started = tmp_path / "uvicorn-started"
    environment = os.environ | {
        "DSM_ENCRYPTION_KEY": os.urandom(16).hex(),
        "DSM_PORT": str(port),
        "DSM_DB_PATH": str(tmp_path / "dsm.sqlite3"),
        "DSM_MCP_ENABLED": "false",
        "DSM_TEST_UVICORN_MARKER": str(server_started),
        "PATH": str(commands) + os.pathsep + os.environ["PATH"],
    }
    subprocess.run(
        ["./start.sh"],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    for _ in range(20):
        if server_started.exists():
            break
        time.sleep(0.1)
    else:
        raise AssertionError("startup did not launch Uvicorn after building the frontend")

    built_index = (frontend_output / "index.html").read_text(encoding="utf-8")
    js_assets = re.findall(r'(?:src|href)="(/assets/[^"?]+\.js)"', built_index)
    assert js_assets, "the built index did not reference a JavaScript asset"
    assert all((frontend_output / asset.lstrip("/")).is_file() for asset in js_assets)
