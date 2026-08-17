# Amy — Hermes MCP connector setup & test

Copilot Studio → **Hermes MCP bridge** → `hermes-api` (book, renewals, sync, commissions, AMS).
This is **separate** from SharePoint native knowledge (blocked by DLP until IT fixes that).

Related: [`amy-getting-started.md`](amy-getting-started.md) ·
[`copilot-mcp-egress-plan.md`](copilot-mcp-egress-plan.md) ·
[`amy-copilot-tool-wiring.md`](amy-copilot-tool-wiring.md) (tool rollout order)

---

## Connection summary

| Item | Value |
|---|---|
| **MCP URL (Copilot)** | `https://hermes-mcp.risksolutionsgroup.net/mcp` |
| **Health** | `https://hermes-mcp.risksolutionsgroup.net/healthz` |
| **Auth** | `API_SERVER_KEY` from `/opt/app/.env` |
| **Header** | `X-API-Key: <key>` **or** `Authorization: Bearer <key>` |
| **DLP** | Classify as **Business** — approved internal API (not generic blocked HTTP) |

---

## Part 1 — Test on hermes-gretch (box)

### 1a. Services up

```bash
curl -s http://127.0.0.1:8788/health          # Hermes API (host port)
curl -s http://127.0.0.1:8081/healthz         # MCP bridge local
```

### 1b. Full smoke (local + public)

```bash
cd /opt/rsg-hermes
API_SERVER_KEY="$(grep '^API_SERVER_KEY=' /opt/app/.env | cut -d= -f2-)" \
  HERMES_API_URL=http://127.0.0.1:8788 \
  CHECK_EGRESS=1 \
  ./scripts/mcp_smoke_test.sh
```

Expect: all **PASS** including `public egress MCP ping`.

### 1c. Manual ping (if script missing)

```bash
API_SERVER_KEY="$(grep '^API_SERVER_KEY=' /opt/app/.env | cut -d= -f2-)"

# Local bridge
curl -s -H "Authorization: Bearer $API_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ping","arguments":{}}}' \
  http://127.0.0.1:8081/mcp

# Public (same path Microsoft uses)
curl -s -H "Authorization: Bearer $API_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ping","arguments":{}}}' \
  https://hermes-mcp.risksolutionsgroup.net/mcp
```

Expect in JSON: `bridge reachable` and backend `http://rsg-hermes-api:8787`.

### 1d. List tools

```bash
curl -s -H "Authorization: Bearer $API_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  https://hermes-mcp.risksolutionsgroup.net/mcp | python3 -m json.tool | head -60
```

### 1e. First live data tool (sync health)

```bash
curl -s -H "Authorization: Bearer $API_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"sync_health","arguments":{}}}' \
  https://hermes-mcp.risksolutionsgroup.net/mcp | python3 -m json.tool | head -40
```

HTTP 500 or error in body usually means Supabase/NowCerts creds on `hermes-api` — bridge is still OK.

---

## Part 2 — Copilot Studio connector

### Prerequisites

- Power Platform **DLP** allows MCP/custom connector to `hermes-mcp.risksolutionsgroup.net` (Business group)
- `API_SERVER_KEY` from 1Password `rsg_infrastructure` or `/opt/app/.env`

### Steps

1. [Copilot Studio](https://copilotstudio.microsoft.com) → open **Amy** agent.
2. **Actions** (or **Tools** / **Connectors**) → **Add an action** → **New connector** / **Model Context Protocol**.
3. **Server URL:** `https://hermes-mcp.risksolutionsgroup.net/mcp`
4. **Authentication:** API key
   - Header name: `X-API-Key` (or Bearer — both work on the bridge)
   - Value: `API_SERVER_KEY` (raw secret, no `Bearer` prefix if using X-API-Key)
5. **Test connection** — should succeed and list tools (`ping`, `sync_health`, `list_renewals`, …).
6. **Save** → enable tools you want for first rollout (start with read-only).
7. **Publish** the agent.

### Agent instructions (minimal)

```text
For agency book data, renewals, sync status, commissions, and AMS search,
use the Hermes MCP tools. Do not invent policy or premium numbers.

For internal SOPs and procedures, use SharePoint knowledge when available.

Never set confirm=true on write tools without explicit human approval.
```

---

## Part 3 — Test in Copilot (chat)

After publish, in the test pane or Teams:

| Step | Ask Amy | Pass |
|---|---|---|
| 1 | “Run Hermes ping” / “Is Hermes up?” | Bridge reachable message |
| 2 | “What’s our sync health?” | Queue/sync JSON or summary (not “I can’t connect”) |
| 3 | “What renewals are in the next 90 days?” | Renewal rows or empty with reason |
| 4 | “Who writes GL in Texas?” | `carrier_appetite` results |

If step 1 works in Copilot but 2–4 fail, Hermes bridge is fine — check Supabase/NowCerts on the box.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| DLP blocks connector add | IT: allow Hermes MCP URL in **Business** DLP (see [`amy-copilot-knowledge-setup.md`](amy-copilot-knowledge-setup.md) DLP section) |
| 401 / Unauthorized in JSON | Wrong `API_SERVER_KEY` in Copilot vs `/opt/app/.env` |
| Ping OK, data tools error | `hermes-api` creds; `curl http://127.0.0.1:8788/health` |
| “invalid token” on AMS tools | `HERMES_API_TOKEN` missing on MCP bridge container |
| Connector add fails (network) | DNS `hermes-mcp` → box IP; nginx TLS reload |
| HTTP 200 but error in body | Read JSON-RPC `error` field — MCP always returns 200 |

---

## Rollout order (after connector works)

See [`amy-copilot-tool-wiring.md`](amy-copilot-tool-wiring.md) Track B:

1. `ping` ✅  
2. `sync_health`  
3. `list_renewals` / `retention_scan` / `carrier_appetite`  
4. `ams_search_insured` / commissions  
5. `hermes_dispatch` last among reads  
6. Writes only with human approval
