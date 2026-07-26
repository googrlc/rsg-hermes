# rsg-hermes MCP bridge

`app.py` is the MCP facade in front of `rsg-hermes-api` — the "one door" every
agent reaches RSG through. It is a thin JSON-RPC proxy: no LLM, no business
logic. The router, the renewal/retention/intake engines and the write gate all
stay in `rsg-hermes`.

> **Captured into version control 2026-07-26.** Until then this file existed in
> exactly one place on earth: inside the running `app-rsg-hermes-mcp-1`
> container, which has **no bind mounts**. Recreating that container would have
> destroyed it with no copy anywhere. The file here is byte-identical to what
> was running (`md5 f71aa0ee9ddcbb52f79373115e55c39e`, 594 lines, 28,344 bytes).

## Deployment reality — read before changing anything

- **The container has no mounts.** `docker inspect app-rsg-hermes-mcp-1` returns
  `Mounts: []`. `app.py` lives at `/app/app.py` inside the container only.
- **Edits have been made in place**, via `docker cp` or `docker exec`. On the box
  the file shows owner `dnsmasq:1000` — a uid collision from container writes,
  harmless in itself but a clear sign more than one process has edited it.
- **Therefore: this repo is now the source of truth.** Change it here, then
  deploy to the container. Do not hand-edit the running copy; the next rebuild
  silently reverts it and nobody will know which version was live.

To deploy a change:

```bash
docker cp deploy/mcp-bridge/app.py app-rsg-hermes-mcp-1:/app/app.py
docker restart app-rsg-hermes-mcp-1
curl -s http://localhost:8081/healthz
```

## Configuration

| Variable | Purpose |
|---|---|
| `API_SERVER_KEY` | The shared bearer that gates the bridge itself |
| `HERMES_API_TOKEN` | Sent as `Authorization: Bearer` to `rsg-hermes-api` |
| `HERMES_API_URL` | Upstream base (defaults to the api service) |

These live in **`/opt/app/.env`**, which is gitignored and untracked — git will
never touch it, but an Elestio redeploy of `/opt/app` can.

### ⚠ `HERMES_API_TOKEN` exists in exactly one place

Verified 2026-07-26: of `/opt/app/.env` and its five `.env.bak*` siblings, **only
the live `.env` contains `HERMES_API_TOKEN`.** Every backup predates it —
including `.env.bak-hermes-token-20260726030630`, whose name suggests otherwise.
That file is the 18-key *pre-change* snapshot; the live `.env` has 19 keys, the
extra one being the token. Restoring from it reproduces the outage rather than
fixing it.

A post-change backup now exists on the box
(`.env.bak-POST-token-fix-20260726-032226`, byte-identical to `.env`), but it
sits on the same disk and would not survive an `/opt/app` rebuild. **Put the
token in 1Password (`rsg_infrastructure`)** — that is the only copy that
survives the failure this protects against.

## The empty-token guard

Lines 38–52 fail startup when `HERMES_API_TOKEN` is set but empty:

```python
if _raw_hermes_token is not None and not _raw_hermes_token.strip():
    raise RuntimeError("HERMES_API_TOKEN is set but empty. ...")
```

On 2026-07-26 `docker-compose.yml` declared `HERMES_API_TOKEN=${HERMES_API_TOKEN:-}`
with nothing in the environment, so the variable existed and was empty. The
bridge then omitted the `Authorization` header entirely and every token-gated
route returned "invalid token" while the configuration looked correct. An empty
token silently disabling auth is the worst version of that failure — it looks
configured. Failing loudly at startup converts a confusing outage into an
obvious one.

The guard is already in this file; there is no separate patch to apply.

## Tools exposed

**Read:** `ping` · `list_renewals` · `retention_scan` · `list_tasks` ·
`list_documents` · `list_commissions` · `commission_rules` · `carrier_appetite` ·
`ams_search_insured` · `sync_health`

**Write:** `hermes_dispatch` · `create_task` · `complete_task` · `create_case` ·
`create_client` · `draft_intake` · `save_document` · `file_to_nextcloud` ·
`ams_create_insured` · `ams_upsert_policy`

`hermes_dispatch` is the keystone: it forwards natural language to `POST
/dispatch`, which returns `requires_confirmation=true` for any write, so the
human-approval gate survives end to end.

There is **no pipeline/opportunity tool** — those writes go over HTTP to
`POST /api/opportunities`. See `.claude/skills/hermes-crm-writer/SKILL.md`.

## Gotcha

MCP-over-HTTP **always returns HTTP 200.** Auth failures come back in the
JSON-RPC body as `-32001 Unauthorized`. Never key a smoke test on the status
code — read the body.
