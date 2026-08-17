#!/usr/bin/env bash
# SharePoint MCP launcher for Cursor cloud/desktop — always uses repo .venv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec "$ROOT/.venv/bin/python3" "$ROOT/sharepoint_mcp.py" "$@"
