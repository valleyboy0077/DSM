"""MCP (Model Context Protocol) server for Dell Server Manager.

Exposes AI agent-accessible tools for hardware monitoring and control.
An AI agent (Claude, Codex, etc.) can connect via MCP to:
  - Query server inventory
  - Read real-time temperatures and fan speeds
  - Get hardware inventory (CPUs, memory, drives, PSUs)
  - View iDRAC settings (network, BIOS, storage, firmware, security)
  - Control fans (mode, speed)
  - Control power (on/off/restart)
  - View system event logs
  - Manage iDRAC users

Transport: SSE (Server-Sent Events) over HTTP — configured via DSM_MCP_PORT.
Authentication: Bearer token (same JWT used by REST API).

Usage (from an AI agent config):
  {
    "command": "python3",
    "args": ["-m", "dsm.mcp.server"],
    "env": {
      "DSM_API_URL": "http://127.0.0.1:8080",
      "DSM_API_TOKEN": "<jwt_token>"
    }
  }
"""

from dsm.mcp.server import main

__all__ = ["main"]
