#!/usr/bin/env python3
"""SharePoint MCP — stdio entry for Cursor and local agents.

Cursor MCP config (project or user settings):

```json
{
  "mcpServers": {
    "sharepoint": {
      "command": "python3",
      "args": ["/absolute/path/to/rsg-hermes/sharepoint_mcp.py"],
      "env": {
        "MS365_TENANT_ID": "...",
        "MS365_CLIENT_ID": "...",
        "MS365_CLIENT_SECRET": "...",
        "SHAREPOINT_SITE_URL": "https://your-tenant.sharepoint.com/sites/RSG-Knowledge"
      }
    }
  }
}
```

Hosted HTTP mode: see deploy/sharepoint_mcp/README.md

For Power Automate + OneDrive + SharePoint in one Cursor MCP, see
docs/microsoft-mcp-cursor-config.md (powerautomate-mcp npm package).

**Tenant / Entra checklist (apps, permissions, secrets):**
[`microsoft-tenant-mcp-setup.md`](docs/microsoft-tenant-mcp-setup.md)
"""

from __future__ import annotations

import os
import sys


def _ensure_venv_python() -> None:
    """Re-exec with repo .venv when not already running in it."""
    root = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(root, ".venv")
    venv_py = os.path.join(venv_dir, "bin", "python3")
    if not os.path.isfile(venv_py):
        return
    # venv python may symlink to the system binary; sys.prefix is the reliable signal.
    if os.path.normpath(sys.prefix) == os.path.normpath(venv_dir):
        return
    try:
        import mcp  # noqa: F401
        return
    except ImportError:
        pass
    os.execv(venv_py, [venv_py, __file__, *sys.argv[1:]])


_ensure_venv_python()

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_CORE = os.path.join(_ROOT, "packages", "rsg-hermes-core")
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)


def main() -> None:
    transport = (os.environ.get("SHAREPOINT_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport in ("http", "streamable-http"):
        import uvicorn

        from deploy.sharepoint_mcp.http_app import app

        host = os.environ.get("SHAREPOINT_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("SHAREPOINT_MCP_PORT", "8082"))
        uvicorn.run(app, host=host, port=port)
        return

    from deploy.sharepoint_mcp.server import run_stdio

    run_stdio()


if __name__ == "__main__":
    main()
