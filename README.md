# Hermes (rsg-hermes)

Python operations engine for Risk Solutions Group's **custom CRM / Command
Center**, backed by **NowCerts** (the AMS system of record) and **Supabase**
(the Command Center database — analytics, queues, KPIs, renewal state). It ships
two entry points:

- **`hermes`** — a one-shot / REPL CLI that runs the scheduled jobs (sync,
  renewals, revenue sentinel, commissions, scorecards, intake execution).
- **`hermes-api`** — the private HTTP backend for the Command Center
  (`rsg-hermes-api:8787`). In production the **`rsg-hermes` MCP bridge** (a
  separate thin facade container) is the public "one door" in front of it; this
  repo is what sits behind that door.

> **Migration state (July 2026).** RSG ran on EspoCRM; it has been
> **decommissioned**, and the inbound **Slack Socket Mode** listener has been
> **retired**. Data now lives in **NowCerts + Supabase**. A number of Espo-era
> CLI flags, `.env` keys, and modules still exist in the tree but are inert
> against systems that are gone — they are called out under
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
| **NowCerts (AMS, system of record)** | `NOWCERTS_API_URL`, `NOWCERTS_USERNAME`, `NOWCERTS_PASSWORD` |
| **Supabase (Command Center DB)** | `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_ROLE_KEY` |
| **`hermes-api` bearer** | `HERMES_API_TOKEN`, `HERMES_API_HOST`, `HERMES_API_PORT` |
| **LLM (advisor / NL agent)** | `HERMES_OPENAI_API_KEY` / `LITELLM_API_KEY` + `LITELLM_BASE_URL` |
| **Slack posting** (outbound only) | `SLACK_THE_BOSS`, `HERMES_SENTINEL_SLACK_CHANNEL`, bot token |
| **Nextcloud (file storage)** | `NEXTCLOUD_URL`, `NEXTCLOUD_USER`, `NEXTCLOUD_APP_PASSWORD` |

`.env.example` still lists `ESPO_*` keys — those are **legacy** and unused now
that EspoCRM is gone. See [`docs/DEPLOY.md`](docs/DEPLOY.md) for the box layout.

## Run

Current, live job families (all NowCerts / Supabase backed):

```bash
# Health & metrics
hermes --ops-doctor              # Supabase connectivity + all Hermes tables (the readiness check)
hermes --snapshot-kpis           # record system/finance/renewal KPIs → dashboard_kpis
hermes --commands                # print the command catalog

# NowCerts → Supabase ingest / enrich
hermes --sync-nowcerts           # pull AMS insureds/policies into the Command Center
hermes --sync-canonical-book     # rebuild the canonical book of business
hermes --enrich-nowcerts         # backfill/enrich NowCerts-sourced records

# Command Center → NowCerts (outbound)
hermes --sync-hub-to-nowcerts    # push queued Command Center changes to the AMS

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
| `rsg-hermes-api` | `hermes-api` | The Command Center HTTP backend, `8788:8787`, on the external `hermes-shared` network. | `unless-stopped` |
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

`hermes-api` is the Command Center backend and the surface the `rsg-hermes` MCP
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

These remain in the tree for history but target systems that are gone. Do **not**
treat them as live:

- **EspoCRM REST path** — `--doctor`, `--ping`, `--kpi`, `--audit-schema`,
  `--inventory-metadata` read through `hermes/core/client.py` (the Espo client).
  EspoCRM is decommissioned, so these no longer reach a live CRM. (`--doctor` is
  still the compose default command only as a harmless no-op read.)
- **EspoCRM write-back / queue** — `--espo-writeback`, `--process-crm-queue`, and
  the several `--sync-*`/`--*-writeback` flags aimed at Espo drain an
  `crm_write_queue` that was dropped in the decommission.
- **`--espo-db-doctor`** — the direct-Postgres read lane was **removed**
  (PR #191, `docs/espocrm-read-lane.md` is historical).
- **Slack Socket Mode** (`--slack`) — the inbound listener was retired July 2026.
  Slack **outbound posting** (sentinel, scorecards, alerts) via the bot token is
  still live.

## TLS Note

Hermes defaults `HERMES_VERIFY_TLS=false` to preserve the Tailscale/IP HTTPS
behavior for tailnet services. Set it to `true` only when the target is reachable
with a matching certificate chain.
