"""Shared helpers for the iDRAC Redfish debug scripts in ``scripts/idrac/``.

Credentials are never hardcoded in this repository. Every script in this
directory reads the target controller and login from environment variables
(or the equivalent CLI flags):

* ``IDRAC_HOST``     -- hostname or IP of the iDRAC, e.g. ``10.1.1.109``
* ``IDRAC_USER``     -- iDRAC account name with sufficient privileges
* ``IDRAC_PASSWORD`` -- password for that account

Example::

    export IDRAC_HOST=10.1.1.109 IDRAC_USER=ned IDRAC_PASSWORD='...'
    python scripts/idrac/check_idrac_users.py

Self-signed iDRAC certificates mean TLS verification is disabled by design
for these LAN-only debugging tools; the urllib3 warning is suppressed so the
output stays readable.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import httpx

try:  # Silence the InsecureRequestWarning noise from self-signed iDRAC certs.
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:  # pragma: no cover - urllib3 always ships with httpx
    pass


def build_client(
    host: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
) -> httpx.Client:
    """Return a configured Redfish client for one iDRAC controller.

    Values passed explicitly (e.g. from CLI flags) win over the environment
    variables documented in the module docstring. Raises ``SystemExit`` with a
    helpful message when any required value is missing.
    """
    host = host or os.environ.get("IDRAC_HOST")
    user = user or os.environ.get("IDRAC_USER")
    password = password or os.environ.get("IDRAC_PASSWORD")

    missing = [
        name
        for name, value in (
            ("IDRAC_HOST", host),
            ("IDRAC_USER", user),
            ("IDRAC_PASSWORD", password),
        )
        if not value
    ]
    if missing or not host or not user or not password:
        sys.exit(
            "Missing credentials: set "
            + ", ".join(missing or ["IDRAC_HOST", "IDRAC_USER", "IDRAC_PASSWORD"])
            + " (see scripts/idrac/redfish_common.py for usage)."
        )

    return httpx.Client(
        base_url=f"https://{host}",
        auth=httpx.BasicAuth(user, password),
        verify=False,  # iDRAC controllers ship self-signed certificates.
        timeout=30,
    )
