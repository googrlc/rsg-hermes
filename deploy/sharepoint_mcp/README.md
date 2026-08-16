# rsg-sharepoint MCP bridge

Read-only SharePoint knowledge tools for Amy Phase 1 — SOPs, carrier guides, training
docs. Uses Microsoft Graph with the same Entra app as mail triage (`MS365_*`).

## Two transports

| Mode | Entry | Use case |
|---|---|---|
| **stdio** | `python3 sharepoint_mcp.py` | Cursor / Claude Desktop (local MCP) |
| **HTTP** | `uvicorn deploy.sharepoint_mcp.http_app:app --host 0.0.0.0 --port 8082` | Hosted on hermes-gretch, Copilot connectors |

Set `SHAREPOINT_MCP_TRANSPORT=http` to run HTTP mode via the root script instead.

## Configuration

| Variable | Purpose |
|---|---|
| `MS365_TENANT_ID` | Entra directory (tenant) id |
| `MS365_CLIENT_ID` | App registration client id |
| `MS365_CLIENT_SECRET` | App secret |
| `SHAREPOINT_SITE_URL` | Default site, e.g. `https://tenant.sharepoint.com/sites/RSG-Knowledge` |
| `API_SERVER_KEY` | Optional bearer for HTTP mode (recommended on the box) |
| `SHAREPOINT_MAX_READ_BYTES` | Max download for `read_document` (default 524288) |
| `SHAREPOINT_MCP_PORT` | HTTP port (default 8082) |

### Entra permissions (application, admin-consented)

- `Sites.Read.All`
- `Files.Read.All`

Mail permissions (`Mail.ReadWrite`) are separate — the same app registration can hold both.

## Cursor MCP config

```json
{
  "mcpServers": {
    "sharepoint": {
      "command": "python3",
      "args": ["/workspace/sharepoint_mcp.py"],
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

Use the **absolute path** to `sharepoint_mcp.py` on your machine. Activate `.venv` first or point `command` at `.venv/bin/python3`.

## Tools

| Tool | Description |
|---|---|
| `ping` | Auth + default site check |
| `list_sites` | Search tenant sites (consolidation inventory) |
| `get_site_info` | Resolve site URL → Graph id |
| `list_libraries` | Document libraries on the site |
| `list_folder` | Browse folders under the default library |
| `search_knowledge` | Search file names/content (read-only) |
| `read_document` | Read plain-text / markdown files by item id |

## Deploy on hermes-gretch

```bash
# From /opt/app after git pull + rebuild, or docker cp for a hotfix:
docker cp sharepoint_mcp.py app-rsg-sharepoint-mcp-1:/app/sharepoint_mcp.py 2>/dev/null || true
docker cp deploy/sharepoint_mcp app-rsg-sharepoint-mcp-1:/app/deploy/ 2>/dev/null || true

# First-time: run via uvicorn on host or add a compose service (port 8082).
source .venv/bin/activate
SHAREPOINT_MCP_TRANSPORT=http API_SERVER_KEY=... \
  uvicorn deploy.sharepoint_mcp.http_app:app --host 127.0.0.1 --port 8082

curl -s http://127.0.0.1:8082/healthz
```

Add `MS365_*` and `SHAREPOINT_SITE_URL` to `/opt/app/.env` (same file as Hermes).

## Smoke test (HTTP)

```bash
curl -s http://127.0.0.1:8082/healthz

curl -s \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ping","arguments":{}}}' \
  http://127.0.0.1:8082/mcp
```

## Source of truth

This repo is the source of truth. Do not hand-edit a running container copy without committing here first.
