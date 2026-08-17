# rsg-sharepoint MCP bridge

Read-only SharePoint knowledge tools for Amy Phase 1 — SOPs, carrier guides, training
docs. Uses Microsoft Graph with the same Entra app as mail triage (`MS365_*`).

> **Not the full Microsoft MCP.** For Power Automate, OneDrive, Excel, and Power Apps
> in Cursor, use **`powerautomate-mcp`** (npm) on your desktop — see
> [`docs/microsoft-mcp-cursor-config.md`](../../docs/microsoft-mcp-cursor-config.md).
> This server is the **Hermes-hosted, app-only, read-only** SharePoint bridge for Amy.

## Two transports

| Mode | Entry | Use case |
|---|---|---|
| **stdio** | `python3 sharepoint_mcp.py` or `.cursor/bin/sharepoint-mcp.sh` | Cursor / Claude Desktop (local MCP) |
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

Use the **absolute path** to `sharepoint_mcp.py` on your machine. The script re-execs
into `.venv` automatically when system `python3` lacks the `mcp` package. You can
also set `"command"` to `.cursor/bin/sharepoint-mcp.sh` or `.venv/bin/python3`.

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

**hermes-gretch has no host `.venv`.** Python, `uvicorn`, and `mcp` live inside the
Docker image — same as `rsg-hermes-api`. Do **not** `source .venv/bin/activate` on the box.

### Start (recommended)

```bash
cd /opt/rsg-hermes
git pull   # must include sharepoint_mcp.py + deploy/sharepoint_mcp/
./scripts/start_sharepoint_mcp.sh
```

That runs `docker compose --env-file /opt/app/.env up -d --build sharepoint-mcp` and
binds **127.0.0.1:8082** only.

Verify:

```bash
curl -s http://127.0.0.1:8082/healthz
```

### Manual compose (if you prefer)

```bash
cd /opt/rsg-hermes   # or /opt/app if that is your compose root
docker compose --env-file /opt/app/.env up -d --build sharepoint-mcp
```

### One-off without compose file update (emergency)

```bash
docker compose --env-file /opt/app/.env build hermes-api
docker run -d --restart unless-stopped \
  --name rsg-sharepoint-mcp \
  -p 127.0.0.1:8082:8082 \
  --env-file /opt/app/.env \
  -e SHAREPOINT_MCP_TRANSPORT=http \
  rsg-hermes-sharepoint-mcp \
  python3 sharepoint_mcp.py
```

(Replace image name with `docker images | grep hermes` if the project prefix differs.)

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
