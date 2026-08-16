# Hermes (rsg-hermes)

Python operations engine for Risk Solutions Group, bridging **Zoho CRM**
(the CRM system of record), **NowCerts** (the AMS system of record), and
**Supabase** (the operations layer — canonical book mirror, queues, KPIs,
renewal state, intake governance). It ships two entry points:

- **`hermes`** — a one-shot / REPL CLI that runs the scheduled jobs (sync,
  renewals, revenue sentinel, commissions, scorecards, intake execution).
- **`hermes-api`** — the private HTTP backend and Command Center operations UI
  host (`rsg-hermes-api:8787`). In production the **`rsg-hermes` MCP bridge** (a
  separate thin facade container) is the public "one door" in front of it; this
  repo is what sits behind that door.

> **Migration state (August 2026).** RSG's CRM system of record is **Zoho CRM**.
> The custom **Command Center CRM** (the Supabase-backed client/pipeline/case
> layer that replaced EspoCRM) has been **decommissioned** — Hermes no longer
> treats Supabase tables as the CRM. The **Command Center web UI**
> (`/command-center/`) remains the operations workstation (intake review,
> renewal desk, dashboards, task queues); it is not the CRM.
>
> EspoCRM was decommissioned July 2026; the inbound **Slack Socket Mode**
> listener has been **retired**. Insured and policy truth live in **NowCerts**;
> Hermes mirrors them into **Supabase** for analytics and syncs client/pipeline
> work to **Zoho**. The Espo client and its CLI flags have been **deleted** from
> the tree; a few Espo-era `.env` keys remain but are unused — see
> [Legacy / decommissioned](#legacy--decommissioned) so a fresh reader does not
> mistake them for live paths.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

The credentials that matter now are:

| Purpose | Vars |
|---|---|
| **Zoho CRM (system of record)** | `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`; optional `ZOHO_DATA_CENTER`, `HERMES_WRITE_TO_ZOHO` |
| **NowCerts (AMS, system of record)** | `NOWCERTS_API_URL`, `NOWCERTS_USERNAME`, `NOWCERTS_PASSWORD` |
| **Supabase (operations DB)** | `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_ROLE_KEY` |
| **`hermes-api` bearer** | `HERMES_API_TOKEN`, `HERMES_API_HOST`, `HERMES_API_PORT` |
| **LLM (advisor / NL agent)** | `HERMES_OPENAI_API_KEY` / `LITELLM_API_KEY` + `LITELLM_BASE_URL` |
| **Slack posting** (outbound only) | `SLACK_THE_BOSS`, `HERMES_SENTINEL_SLACK_CHANNEL`, bot token |
| **Nextcloud (file storage)** | `NEXTCLOUD_URL`, `NEXTCLOUD_USER`, `NEXTCLOUD_APP_PASSWORD` |

The `ESPO_*` keys are gone from `.env.example` — nothing reads them. See
[`docs/DEPLOY.md`](docs/DEPLOY.md) for the box layout.

## Run

Current, live job families (all NowCerts / Supabase backed):

```bash
# Health & metrics
hermes --ops-doctor              # Supabase connectivity + all Hermes tables (the readiness check)
hermes --snapshot-kpis           # record system/finance/renewal KPIs → dashboard_kpis
hermes --commands                # print the command catalog

# NowCerts → Supabase ingest / enrich
hermes --sync-nowcerts           # pull AMS insureds/policies into the canonical book
hermes --sync-canonical-book     # rebuild the canonical book of business
hermes --enrich-nowcerts         # backfill/enrich NowCerts-sourced records

# Supabase queue → NowCerts (outbound AMS writes)
hermes --sync-hub-to-nowcerts    # push queued outbound_sync_queue changes to the AMS

# Renewals (Project 85)
hermes --renewal-refresh         # refresh renewal pipeline state from the book
hermes --renewal-classify        # classify upcoming renewals into cadence buckets
hermes --renewal-executor        # execute queued, approved renewal actions
hermes --run-renewal-executor-worker

# Revenue Sentinel (reads Supabase since July 2026)
hermes --revenue-sentinel                # proactive briefing → Slack
hermes --revenue-sentinel-dry-run        # preview without posting
hermes --revenue-sentinel-health         # freshness + config check
hermes --revenue-sentinel-force          # bypass same-day idempotency

# Commissions
hermes --commission-audit                # audit expected vs actual → Slack
hermes --commission-ingest               # ingest a staged commission batch
hermes --commission-reconcile-file ./statements/carrier.csv
hermes --sync-commissions

# Scorecards / changelog
hermes --eom-scorecard                   # end-of-month scorecard → Slack
hermes --changelog                       # recent-activity changelog

# Intake / casework executors (queue → NowCerts/Supabase)
hermes --run-intake-worker --intake-poll-seconds 5
hermes --intake-executor
hermes --casework-executor
hermes --quote-executor

# Executor scheduler (single-instance, backoff, dead-letter) — off by default
hermes --run-scheduler --scheduler-interval 300 --scheduler-batch 10

# Natural-language one-shots (LLM agent)
hermes 'renewal audit'
hermes 'total premium for Acme'
hermes 'research business Acme Plumbing Atlanta'
```

Most `--*` jobs accept a matching `--*-dry-run` (and executors an `--*-limit`).
Run `hermes --commands` for the full catalog.

## Docker

`docker-compose.yml` defines the containers that run on the hermes-gretch box:

| Container | Service | Role | Restart |
|---|---|---|---|
| `rsg-hermes` | `hermes` | Per-cron runner — jobs run as `docker compose run --rm hermes hermes --<job>`. Default command is a harmless read-only check; **not** an always-on listener. | `no` |
| `rsg-hermes-api` | `hermes-api` | Hermes HTTP backend + Command Center operations UI, `8788:8787`, on the external `hermes-shared` network. | `unless-stopped` |
| `rsg-hermes-intake-worker` | `hermes-intake-worker` | Polls the intake queue (`--run-intake-worker`, 5s). | `unless-stopped` |
| `rsg-hermes-scheduler` | `hermes-scheduler` | Executor scheduler — drains `outbound_sync_queue` → NowCerts (renewal, intake, quote, casework, opportunity-writeback). **Disabled by default** — gated behind the `scheduler` compose profile *and* `SCHEDULER_ENABLED`. | `unless-stopped` |

```bash
docker compose up -d --build                              # api + workers (no scheduler, no listener)
docker compose --profile scheduler up -d hermes-scheduler # opt into the scheduler
docker compose run --rm hermes hermes --ops-doctor        # one-shot job / cron pattern
docker compose logs -f hermes-api
```

Because Slack Socket Mode is retired and `restart` on the `hermes` service is
`no`, a plain `docker compose up -d` no longer keeps a listener container alive —
recurring work is driven by **scheduled `docker compose run` invocations**, not a
24/7 loop.

> The `hermes-crm-queue-worker` service was **removed 2026-07-21** — it drained
> the EspoCRM-era `crm_write_queue`, which no longer exists.

## Private API bridge (`hermes-api`)

`hermes-api` is the Hermes HTTP backend. It serves the Command Center
operations UI (`/command-center/`) and is the surface the `rsg-hermes` MCP
bridge proxies:

```bash
hermes-api --host 127.0.0.1 --port 8787
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/api/hermes/sync-health   # queue depth / freshness (MCP `sync_health`)
curl http://127.0.0.1:8787/api/carriers             # read-only carrier appetite
```

Set `HERMES_API_TOKEN` to require `Authorization: Bearer <token>`. Write
commands return `requires_confirmation` unless called with `confirm=true`.

> **MCP-over-HTTP gotcha:** the bridge always returns HTTP 200; auth failures
> come back in the JSON-RPC body (`-32001 Unauthorized`). Never key a smoke test
> on the HTTP status — read the body.

## Operations Center (Supabase governance)

Hermes carries a Supabase-backed governance layer (the "Operating Constitution").
See [`docs/hermes-operating-constitution.md`](docs/hermes-operating-constitution.md).

Key live capabilities:
- **`--ops-doctor`** — verifies Supabase connectivity and the Hermes tables.
- **`--snapshot-kpis`** — records system/finance/renewal metrics to `dashboard_kpis`.
- **Slack Router** — registry-aware posting that refuses unregistered channels.
- **Renewal Tracker** — Project 85 lifecycle state in Supabase.

Schema lives in `supabase/migrations/`; operations modules in `hermes/operations/`.

## Legacy / decommissioned

Three CRM surfaces are gone — not merely inert:

| Surface | Decommissioned | Replaced by |
|---|---|---|
| **EspoCRM** | July 2026 | Command Center CRM (July 2026) → Zoho CRM (August 2026) |
| **Command Center CRM** | August 2026 | **Zoho CRM** (system of record) |
| **Slack Socket Mode** (`--slack`) | July 2026 | Outbound Slack posting only (sentinel, scorecards, alerts) |

The **EspoCRM code path is deleted**. `hermes/core/client.py` (the Espo REST
client), `hermes/core/auditor.py`, and `hermes/core/schema_map.py` were removed,
along with the flags that only made sense against Espo metadata (`--doctor`,
`--audit-fields`, `--audit-schema`, `--inventory-metadata`) and the
write-back/queue flags (`--espo-writeback`, `--process-crm-queue`,
`--espo-db-doctor`). The two survivors were repointed:

- `--ping` reports on Hermes itself; it no longer proves a CRM connection.
- `--kpi` prints the latest `agency_snapshots` row (clients, policies, active
  premium, retention) instead of Espo entity counts.

For readiness checks use **`--ops-doctor`** (Supabase connectivity + Hermes
tables). `docs/espocrm-read-lane.md` is historical (the direct-Postgres read lane
was removed in PR #191). Zoho field packs and backfill scripts live under
`docs/zoho/`; the Supabase `agency_crm_*` tables are legacy tail — do not treat
them as the CRM.

Still in the tree but retired:

- **Slack Socket Mode** (`--slack`) — the inbound listener was retired July 2026.
  Slack **outbound posting** (sentinel, scorecards, alerts) via the bot token is
  still live.

## TLS Note

Hermes defaults `HERMES_VERIFY_TLS=false` to preserve the Tailscale/IP HTTPS
behavior for tailnet services. Set it to `true` only when the target is reachable
with a matching certificate chain.
