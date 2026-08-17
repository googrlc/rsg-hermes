# Amy Copilot — wire tools one at a time

Sequential rollout for Lamar (Operations) after **Obsidian → SharePoint** migration and
**Hermes MCP egress** are live. Do not enable writes until every read tool in a tier passes.

Related: [`sharepoint-migration-status.md`](sharepoint-migration-status.md) ·
[`amy-getting-started.md`](amy-getting-started.md) ·
[`copilot-mcp-egress-plan.md`](copilot-mcp-egress-plan.md) ·
[`deploy/sharepoint_mcp/README.md`](../deploy/sharepoint_mcp/README.md)

---

## Connectors (two doors)

| Connector | Copilot URL | Port on box | Purpose |
|---|---|---|---|
| **Hermes** (live) | `https://hermes-mcp.risksolutionsgroup.net/mcp` | 8081 | Book, renewals, sync, commissions, AMS |
| **SharePoint** (Phase 1) | `https://sharepoint-mcp.risksolutionsgroup.net/mcp` | 8082 | Read agency knowledge from RSG-Knowledge |

Both use the same secret: `API_SERVER_KEY` from `/opt/app/.env` as `X-API-Key` or
`Authorization: Bearer`.

**Phase 1 grounding (optional, parallel):** In Copilot Studio → Knowledge → SharePoint,
add **only** the **RSG-Knowledge** site. That gives Amy Q&A without MCP. MCP tools below
are for browse/search/read-by-id when grounding is thin or you need explicit citations.

---

## Before each tool

On **hermes-gretch**:

```bash
cd /opt/rsg-hermes   # or /opt/app — your checkout path
source .venv/bin/activate

# Hermes bridge
curl -s http://127.0.0.1:8081/healthz

# SharePoint MCP (after install — see scripts/install_sharepoint_mcp_egress.sh)
curl -s http://127.0.0.1:8082/healthz

# Full smoke
HERMES_API_URL=http://127.0.0.1:8788 CHECK_EGRESS=1 API_SERVER_KEY="$API_SERVER_KEY" \
  ./scripts/mcp_smoke_test.sh
API_SERVER_KEY="$API_SERVER_KEY" ./scripts/sharepoint_mcp_smoke_test.sh
```

In **Copilot Studio**: add connector → Streamable HTTP → paste URL → API key auth →
**Test connection**. Then chat-test the prompt in the table below.

Mark each row ✅ in this doc (or your ops log) when Copilot returns real data, not a
generic apology.

---

## Track A — SharePoint MCP (RSG-Knowledge)

Prereqs: `MS365_*` + `SHAREPOINT_SITE_URL` in `/opt/app/.env`; SharePoint MCP on 8082;
DNS `sharepoint-mcp` → box IP; nginx vhost installed.

| # | Tool | Wire step | Amy test prompt | Pass criteria |
|---|---|---|---|---|
| A0 | *(grounding)* | Knowledge → SharePoint → RSG-Knowledge only | "Where is the COI SOP?" | Answer cites SharePoint path under `01-operations/` or `02-personal-lines/` |
| A1 | `ping` | Add SharePoint MCP connector; test connection | "Run SharePoint ping" | Auth OK + default site name |
| A2 | `get_site_info` | Same connector (auto-listed) | "What SharePoint site is configured for knowledge?" | `webUrl` ends with `/sites/RSG-Knowledge` |
| A3 | `list_libraries` | — | "List document libraries on RSG-Knowledge" | At least **Documents** drive |
| A4 | `list_folder` | — | "List folders at the root of RSG-Knowledge" | Sees `00-meta`, `01-operations`, `02-personal-lines`, … |
| A5 | `list_folder` | — | "List files in 00-meta on RSG-Knowledge" | `site-index.md`, `migration-log.md` |
| A6 | `search_knowledge` | — | "Search SharePoint for renewal playbook" | Hits under `01-operations/renewals/` or `02-personal-lines/` |
| A7 | `read_document` | — | "Read site-index.md from SharePoint" (Amy may search first, then read by id) | Markdown body, not empty |
| A8 | `search_knowledge` | — | "How do we process a COI?" | Matches Gretchen COI / service-desk SOP content |
| A9 | `list_sites` | **Admin only** — optional | "Search SharePoint sites matching RSG" | Tenant site list (inventory); not for daily Amy |

After A8 passes, Phase 1 knowledge is production-ready for operators.

---

## Track B — Hermes MCP (book + ops)

Prereqs: Hermes connector live (`ping` ✅); Supabase + NowCerts creds for data tools.

### Tier B1 — Health & renewals (Lamar daily)

| # | Tool | Amy test prompt | Pass criteria |
|---|---|---|---|
| B1 | `ping` | *(done)* | "Hermes is up" / bridge reachable |
| B2 | `sync_health` | "What's our sync health?" | Queue counts, last run — not HTTP 500 |
| B3 | `list_renewals` | "What renewals are in the next 90 days?" | Rows with expiry / premium |
| B4 | `retention_scan` | "Who is at retention risk?" | At-risk list or empty with reason |
| B5 | `carrier_appetite` | "Who writes GL in Texas?" | Carrier rows from appetite table |

### Tier B2 — Book & commissions

| # | Tool | Amy test prompt | Pass criteria |
|---|---|---|---|
| B6 | `ams_search_insured` | "Search AMS for [known client name]" | Insured match from NowCerts |
| B7 | `list_commissions` | "Show commission shortfalls" | Ledger rows or reconciled summary |
| B8 | `commission_rules` | "What commission rate do we expect from [carrier]?" | Rule rows |
| B9 | `list_documents` | "List documents for [client name]" | Index rows (needs Nextcloud path data) |
| B10 | `list_nextcloud_folder` | "List Nextcloud folder for [client]" | WebDAV listing |

### Tier B3 — Cases & intake (legacy Supabase CRM tables)

| # | Tool | Amy test prompt | Pass criteria |
|---|---|---|---|
| B11 | `list_cases` | "What open service cases do we have?" | Case list with progress |
| B12 | `case_progress` | "What's blocking case [id or client]?" | Task checklist state |
| B13 | `list_tasks` | "What's on Gretchen's task queue?" | Open tasks |
| B14 | `list_intake_queue` | "What's waiting in intake?" | Submissions awaiting approval |

### Tier B4 — Router (use after B1–B3 stable)

| # | Tool | Amy test prompt | Pass criteria |
|---|---|---|---|
| B15 | `hermes_dispatch` | "Find client Bull Dawg and summarize policies" | NL routed answer; **no** `confirm=true` on writes |

Add to Amy instructions: *"For book lookups prefer explicit tools; use hermes_dispatch when
the question spans multiple domains."*

### Tier B5 — Writes (approval required)

Do not smoke-test writes in production without Lamar explicit approval. Each write tool
returns `requires_confirmation=true` on first call.

| Tool | Human gate |
|---|---|
| `create_task`, `complete_task`, `create_case` | Assignee email must be `.net` agency user |
| `draft_intake`, `create_client` | Intake / CRM migration path |
| `ams_create_insured`, `ams_upsert_policy` | `confirm=true` only after human OK |
| `ams_push_task`, `ams_push_case`, `ams_drain_casework` | Named `approved_by` + drain preview |
| `save_document`, `file_to_nextcloud`, `ensure_nextcloud_folders` | Path confirmation |
| `add_deck_card` | Board/list names exact |

---

## Copilot Studio tips

1. **One connector per bridge** — Hermes and SharePoint are separate MCP servers; add both.
2. **Tool gating via instructions** — Until a tier passes, tell Amy: *"Do not use
   `list_commissions` yet"* (replace with the next tool you're testing).
3. **Parse JSON-RPC errors** — HTTP 200 with `-32001` means wrong `API_SERVER_KEY`.
4. **Timeouts** — Renewals/AMS calls can take 15–30s; Copilot connector timeout ≥ 60s if configurable.
5. **Deck / Nextcloud tools** — Require Nextcloud creds on `hermes-api`; expect 500 until set.

---

## Box deploy checklist (SharePoint MCP)

```bash
cd /opt/rsg-hermes
git pull origin cursor/amy-tool-wiring-e461   # after merge

# MS365 + site URL must already be in /opt/app/.env
grep -E '^(MS365_|SHAREPOINT_SITE_URL)' /opt/app/.env

# Start SharePoint MCP (Docker — hermes-gretch has no host .venv)
export HERMES_ENV_FILE=/opt/app/.env
docker compose --env-file /opt/app/.env up -d --force-recreate sharepoint-mcp

# Public egress (nginx + DNS)
sudo ./scripts/install_sharepoint_mcp_egress.sh

# Smoke
API_SERVER_KEY="$(grep '^API_SERVER_KEY=' /opt/app/.env | cut -d= -f2-)" \
  ./scripts/sharepoint_mcp_smoke_test.sh
CHECK_EGRESS=1 API_SERVER_KEY="$(grep '^API_SERVER_KEY=' /opt/app/.env | cut -d= -f2-)" \
  ./scripts/sharepoint_mcp_smoke_test.sh
```

Wix DNS: **A** `sharepoint-mcp` → `152.53.201.154` (same as `hermes-mcp`).

---

## What we wire next (suggested order for L)

1. **A0–A8** SharePoint knowledge (migration complete — verify content paths)
2. **B2** `sync_health` (queue was stale in Supabase audit)
3. **B3–B5** Lamar renewal desk
4. **B6–B8** book + commissions
5. **B11–B14** Gretchen service desk
6. **B15** dispatch
7. **B5** writes one at a time with explicit approval
