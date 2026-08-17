# hermes-gretch credentials & health checklist

Run on the **hermes-gretch** box (or dev machine with the same `.env`) before
wiring Copilot Studio to the MCP bridge. No SharePoint required.

Related: [`amy-getting-started.md`](amy-getting-started.md) ·
[`scripts/mcp_smoke_test.sh`](../scripts/mcp_smoke_test.sh) ·
[`scripts/hermes_gretch_health.sh`](../scripts/hermes_gretch_health.sh)

---

## 1. Environment variables

Confirm in `/opt/app/.env` (or local `.env`):

| Variable | Required for |
|---|---|
| `SUPABASE_URL` | All Hermes tables, ops-doctor, read tools |
| `SUPABASE_SERVICE_ROLE_KEY` | Same |
| `NOWCERTS_USERNAME` / `NOWCERTS_PASSWORD` | AMS reads, sync, `ams_search_insured` |
| `HERMES_API_TOKEN` | Bearer on privileged `/api/hermes/*` routes |
| `API_SERVER_KEY` | MCP bridge auth (Copilot presents this) |
| `HERMES_API_URL` | Bridge upstream (default `http://rsg-hermes-api:8787`) |

Optional for extended smoke:

| Variable | Required for |
|---|---|
| `ZOHO_*` | Zoho CRM writes / backfill |
| `OPENAI_API_KEY` or LiteLLM | NL agent, `--ops-doctor` LLM probe |
| `SUPERMEMORY_API_KEY` | Document library recall |

**1Password:** `rsg_infrastructure` — especially `HERMES_API_TOKEN` (see bridge README).

---

## 2. One-shot health script

```bash
cd /opt/rsg-hermes   # or repo root on dev
source .venv/bin/activate
./scripts/hermes_gretch_health.sh
```

Runs `hermes --ops-doctor`, API `/health`, optional MCP smoke if bridge is up.

---

## 3. Manual checks

### Ops doctor

```bash
hermes --ops-doctor
```

Expect green on Supabase connectivity and Hermes tables. Red on missing creds is
expected until `.env` is complete.

### Hermes API

```bash
curl -s http://127.0.0.1:8787/health
curl -s http://127.0.0.1:8787/api/command-center/skills | head -c 300
```

### MCP bridge

```bash
curl -s http://127.0.0.1:8081/healthz
API_SERVER_KEY="$(grep API_SERVER_KEY /opt/app/.env | cut -d= -f2-)" \
  ./scripts/mcp_smoke_test.sh
```

### Sync health (needs Supabase + queue history)

Via MCP or API with token:

```bash
curl -s -H "Authorization: Bearer $HERMES_API_TOKEN" \
  http://127.0.0.1:8787/api/hermes/sync-health | python3 -m json.tool
```

Or MCP `tools/call` → `sync_health`.

### Book sync health (NowCerts + mirror)

```bash
curl -s -H "Authorization: Bearer $HERMES_API_TOKEN" \
  http://127.0.0.1:8787/api/hermes/book-sync | python3 -m json.tool
```

---

## 4. Expected failures without creds

| Check | Without creds |
|---|---|
| `/health` | OK |
| `/api/command-center/skills` | OK |
| Most `/api/*` dashboards | HTTP 500 |
| `--ops-doctor` | Fails fast on missing `SUPABASE_URL` |
| `sync_health` / AMS tools | Error in tool body |

That is normal in dev until secrets are set.

---

## 5. After creds are set

- [ ] `hermes --ops-doctor` exits 0
- [ ] `sync_health` shows recent `outbound_sync_queue` completion
- [ ] `list_renewals` / `ams_search_insured` return data in MCP smoke
- [ ] Document egress URL in [`copilot-mcp-egress-plan.md`](copilot-mcp-egress-plan.md)
