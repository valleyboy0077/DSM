#!/usr/bin/env python3
"""Regression test for DSM frontend authenticated mutation requests.

The frontend request helper must merge per-call JSON headers with the global
Authorization header. If a POST/PUT call replaces the header object entirely,
protected mutations return 401, the SPA removes dsm_token, and the user is
kicked back to login without the change being saved.
"""
from pathlib import Path

api_ts = Path("frontend/src/api.ts").read_text()

assert "const mergedHeaders" in api_ts or "const requestHeaders" in api_ts, (
    "api.ts request() must build merged headers before fetch"
)
assert "...(options?.headers" in api_ts, (
    "api.ts request() must include per-request headers in the merged header object"
)
assert "Authorization" in api_ts, "api.ts request() must preserve Authorization header"

fetch_call = api_ts[api_ts.index("fetch(`${API}${path}`") : api_ts.index("if (resp.status === 401)")]
assert "headers: mergedHeaders" in fetch_call or "headers: requestHeaders" in fetch_call, (
    "fetch() must pass the merged header object, not the original base headers overwritten by options"
)
assert "...options" in fetch_call, "fetch() must still pass method/body/etc options"

print("frontend auth header merge regression test passed")
