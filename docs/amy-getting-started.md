# Amy — Getting Started

Amy is RSG's **intelligence and orchestration layer**: one assistant persona that routes
staff questions to the right system-of-record or Hermes tool. She is **not** a CRM, AMS,
database, or replacement for Zoho, NowCerts, or SharePoint.

Full architecture and governance live in [`rsg-digital-operating-system.md`](rsg-digital-operating-system.md).

## What "orchestrator" means here

| Layer | Role |
|---|---|
| **Amy (Copilot Studio)** | User experience — natural language in, routed answers out |
| **`rsg-hermes` MCP bridge** | Single public "one door" — MCP tool surface, no LLM |
| **`hermes-api`** | Domain logic, read tools, approval-gated writes |
| **Zoho / NowCerts / Supabase / SharePoint** | Systems of record and mirrors |

Amy does **not** call source systems directly. Every agency action flows:

```text
User → Copilot Studio (Amy) → MCP bridge (:8081) → hermes-api (:8787) → backends
```

Specialized capabilities (renewals, commissions, carrier appetite, intake) are **tools behind
one assistant**, not separate visible agents.

## What must be running

On the **hermes-gretch** box (or your dev machine):

| Service | Typical name | Port | Health |
|---|---|---|---|
| Hermes API | `rsg-hermes-api` / `app-rsg-hermes-api-1` | 8787 | `GET /health` |
| MCP bridge | `app-rsg-hermes-mcp-1` | 8081 | `GET /healthz` |

The MCP bridge is **not** in this repo's `docker-compose.yml`; it is deployed separately.
Source of truth for the bridge code: [`deploy/mcp-bridge/app.py`](../deploy/mcp-bridge/app.py).

## Prerequisites

### Bridge + API tokens

| Variable | Where | Purpose |
|---|---|---|
| `API_SERVER_KEY` | MCP bridge | Bearer token Copilot presents to the bridge |
| `HERMES_API_TOKEN` | MCP bridge → API | Bearer sent upstream to `hermes-api` |
| `HERMES_API_URL` | MCP bridge | Upstream base (default `http://rsg-hermes-api:8787`) |

On production these live in `/opt/app/.env` (gitignored). **Back up `HERMES_API_TOKEN` in
1Password** — backup `.env.bak*` files on the box may not contain it. See
[`deploy/mcp-bridge/README.md`](../deploy/mcp-bridge/README.md).

**Empty-token guard:** if `HERMES_API_TOKEN` is set but blank, the bridge refuses to start.
Unset it to run anonymously, or set a real value.

### Live data (Phase 2+)

Read tools need the same credentials as `hermes-api`:

- **Supabase** — `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- **NowCerts** — `NOWCERTS_*`
- **Zoho** — `ZOHO_*` (CRM reads post-migration)

Without these, `/health` still works; most `/api/*` routes return 500 — expected until creds
are set.

### Microsoft (Amy UX)

- Copilot Studio license and environment for RSG
- **Phase 1:** SharePoint site(s) with agency knowledge (SOPs, carrier guides, training)
- **Phase 2:** MCP connector in Copilot Studio pointed at the bridge URL

## Smoke tests (do this before Copilot)

Run all checks in one shot (after API and bridge are up):

```bash
source .venv/bin/activate
API_SERVER_KEY=dev-key ./scripts/mcp_smoke_test.sh
```

Or step through manually:

### 1. Hermes API

```bash
curl -s http://127.0.0.1:8787/health
curl -s http://127.0.0.1:8787/api/command-center/skills | head -c 500
```

### 2. MCP bridge

```bash
curl -s http://127.0.0.1:8081/healthz
```

### 3. MCP `ping` tool (read the JSON body, not HTTP status)

```bash
curl -s \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ping","arguments":{}}}' \
  http://127.0.0.1:8081/mcp
```

Expect `"text":"rsg-hermes bridge reachable..."` in the result. Auth failures return **HTTP
401** with `-32001 Unauthorized` in the body (older clients may still see HTTP 200 — always
parse the body).

### 4. List advertised tools

```bash
curl -s \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  http://127.0.0.1:8081/mcp | python3 -m json.tool | head -40
```

### Local dev (API already on 8787)

```bash
source .venv/bin/activate
API_SERVER_KEY=dev-key HERMES_API_URL=http://127.0.0.1:8787 \
  uvicorn deploy.mcp-bridge.app:app --host 127.0.0.1 --port 8081
```

## Phase 1 — SharePoint MCP (Cursor + hosted)

Repo entry point: [`sharepoint_mcp.py`](../sharepoint_mcp.py) (stdio for Cursor) and
[`deploy/sharepoint_mcp/`](../deploy/sharepoint_mcp/) (HTTP on port **8082**).

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
        "SHAREPOINT_SITE_URL": "https://tenant.sharepoint.com/sites/RSG-Knowledge"
      }
    }
  }
}
```

Entra app needs **Sites.Read.All** and **Files.Read.All** (application, admin-consented).
See [`deploy/sharepoint_mcp/README.md`](../deploy/sharepoint_mcp/README.md).

**Consolidating several sites into one?** See
[`sharepoint-knowledge-consolidation.md`](sharepoint-knowledge-consolidation.md).

**Power Automate, OneDrive, Power Apps in Cursor?** See
[`microsoft-mcp-cursor-config.md`](microsoft-mcp-cursor-config.md) — that is a separate
npm MCP (`powerautomate-mcp`), not this Hermes SharePoint server.

**Entra / tenant checklist (2 apps + secrets):** [`microsoft-tenant-mcp-setup.md`](microsoft-tenant-mcp-setup.md)

## Copilot Studio wiring (Phase 2)

1. **Create the Amy agent** in Copilot Studio — one assistant, RSG persona and guardrails.
2. **Phase 1 grounding** — connect SharePoint knowledge (no MCP required for basic Q&A on
   procedures and SOPs).
3. **Add MCP connector** (Streamable HTTP):
   - **URL:** reachable from Microsoft cloud (public HTTPS or approved egress path). On the
     tailnet, `http://hermes-gretch:8081/mcp` works from devices on Tailscale only — Copilot
     Studio needs a path Microsoft can reach (reverse proxy, Azure relay, or similar).
   - **Auth:** `Authorization: Bearer <API_SERVER_KEY>` (also accepts `X-API-Key`).
   - **Methods:** POST + GET stream on `/mcp` (bridge implements OPTIONS, keepalive SSE, and
     `Accept: application/json` responses for JSON-only clients).
4. **Smoke in Copilot** — ask Amy to run equivalent of `sync_health` or `list_renewals` and
   confirm tool output appears.
5. **Identity (future)** — pass authenticated user through to the bridge so
   `hermes-api` enforces per-operator permissions. See
   [`identity-permissions-matrix.md`](identity-permissions-matrix.md).

### "Failed to add connector" troubleshooting

| Symptom | Likely cause |
|---|---|
| Connector add fails immediately | Wrong URL, TLS, or Microsoft cannot reach the host |
| 401 on probe | `API_SERVER_KEY` mismatch |
| Parse / stream errors | Client `Accept` header — bridge now honors `application/json` |
| Tools return "invalid token" | `HERMES_API_TOKEN` missing or empty on bridge |
| HTTP 200 but error in body | Read JSON-RPC `error` field (`-32001` = bridge auth) |

## Rollout phases

Aligned with [`rsg-digital-operating-system.md`](rsg-digital-operating-system.md):

| Phase | Goal | Amy capability |
|---|---|---|
| **1 — Knowledge** | Obsidian → SharePoint, organized by function | SharePoint MCP + Copilot native grounding |
| **2 — Read-only** | Supabase, Zoho, NowCerts, SharePoint via Hermes | Safe retrieval through MCP read tools |
| **3 — Controlled automation** | Renewals, commissions, intake, service | Approval-gated writes (`hermes_dispatch`, tokens) |

Start Phase 1 in parallel with bridge smoke tests — staff can use Amy on procedures before
live book data is wired.

## MCP tools exposed today

Read-oriented tools include `ping`, `list_renewals`, `retention_scan`, `sync_health`,
`carrier_appetite`, `ams_search_insured`, `list_commissions`, `list_documents`, and more.
Writes go through `hermes_dispatch`, `create_task`, `draft_intake`, AMS push tools, etc., with
`requires_confirmation` preserved end to end.

Full bridge catalog: [`deploy/mcp-bridge/README.md`](../deploy/mcp-bridge/README.md).

Hermes runtime tool map (what `hermes-api` advertises to the NL agent): regenerate with
`python scripts/gen_tool_map.py` → [`hermes-tool-map.md`](hermes-tool-map.md).

## Deploy a bridge change

```bash
docker cp deploy/mcp-bridge/app.py app-rsg-hermes-mcp-1:/app/app.py
docker restart app-rsg-hermes-mcp-1
curl -s http://localhost:8081/healthz
```

Do not hand-edit the running container copy — this repo is the source of truth.

## Related docs

- [`rsg-digital-operating-system.md`](rsg-digital-operating-system.md) — north star and governance
- [`identity-permissions-matrix.md`](identity-permissions-matrix.md) — operator roles and write tiers
- [`hermes-gretch-health-checklist.md`](hermes-gretch-health-checklist.md) — creds + ops-doctor on the box
- [`copilot-mcp-egress-plan.md`](copilot-mcp-egress-plan.md) — public path for Copilot → MCP bridge
- [`hermes-tool-map.md`](hermes-tool-map.md) — live Hermes tool catalog
- [`deploy/mcp-bridge/README.md`](../../deploy/mcp-bridge/README.md) — bridge deployment reality
