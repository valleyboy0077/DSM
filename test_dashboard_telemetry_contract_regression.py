"""Compile-time contract coverage for cache-first dashboard telemetry."""

from pathlib import Path
import subprocess


REPOSITORY = Path(__file__).resolve().parent


def test_dashboard_maps_failed_telemetry_to_offline():
    """Failed telemetry must drive cards and fleet counts as offline."""
    dashboard = (REPOSITORY / "frontend/src/pages/Dashboard.tsx").read_text(encoding="utf-8")

    assert "status === 'error' || status === 'unreachable' ? 'offline'" in dashboard


def test_dashboard_telemetry_types_match_contract_fixtures():
    """Keep the typed snapshot fixtures valid without adding a frontend test runner."""
    subprocess.run(
        [
            "npm",
            "--prefix",
            "frontend",
            "exec",
            "--",
            "tsc",
            "--noEmit",
            "--project",
            "frontend/tsconfig.json",
        ],
        cwd=REPOSITORY,
        check=True,
        text=True,
        capture_output=True,
    )
