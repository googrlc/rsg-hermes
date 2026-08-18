# Zoho CRM ↔ Supabase sync design (draft)

Short design note bridging today's **NowCerts → Supabase** book sync to the
target **Zoho CRM** system of record. Assumes the committed architecture in
`README.md`: Zoho is CRM SOR, NowCerts is AMS SOR, Supabase is the operations
layer.

**Status:** draft — implementation is partial (`HERMES_WRITE_TO_ZOHO` on intake
commit, `scripts/backfill_zoho_from_momentum.py` for one-shot backfill).

---

## Systems of record

| Domain | System of record | Hermes mirror / ops layer |
|---|---|---|
| Insured / client identity (CRM) | **Zoho CRM** (`Accounts`, `Contacts`) | `canonical_clients` (NowCerts-sourced book) |
| Pipeline / opportunities | **Zoho CRM** (`Deals`) | `opportunities` (intake + legacy rows) |
| Service cases / tasks | **Zoho CRM** (target) | `agency_crm_cases` / `agency_crm_tasks` (legacy tail) |
| Policies / in-force book | **NowCerts** (AMS) | `canonical_policies` |
| Renewal watchlist (Project 85) | **Supabase** (ops state) + Zoho `Renewals` (target) | `project_85_renewals` |
| Outbound AMS writes | **NowCerts** | `outbound_sync_queue` → executors |
| Commissions / finance | **Supabase** | `commission_ledger`, audits |
| Documents | **Nextcloud** | folder URLs stamped on Zoho Accounts |

Supabase is **not** the CRM. It holds mirrors, queues, KPIs, intake governance,
and renewal ops state so Hermes can run fast reads and audited writes without
treating Postgres tables as the CRM.

---

## What runs today

### NowCerts → Supabase (canonical book)

| Job | Direction | Cadence (typical) | Tables |
|---|---|---|---|
| `hermes --sync-nowcerts` | AMS → Supabase | Daily incremental | `canonical_clients`, `canonical_policies` |
| `hermes --sync-canonical-book` | Rebuild book | On demand / repair | same |
| `hermes --enrich-nowcerts` | AMS → Supabase | On demand | enrichment columns |

Freshness: compare NowCerts live book vs mirror via `GET /api/hermes/book-sync`
(`hermes/book_sync/health.py`) — policy counts, tombstones, carrier premium drift.

### Supabase → NowCerts (outbound AMS)

| Path | Direction | Cadence | Gate |
|---|---|---|---|
| `outbound_sync_queue` executors | Supabase queue → NowCerts | Scheduler every 5 min (`SCHEDULER_ENABLED`) | `approved_by` + `approved_at` |

Freshness: `GET /api/hermes/sync-health` → `latest_completed.updated_at` on
`outbound_sync_queue` (most recent completed AMS job).

### Intake → Zoho (opt-in)

| Path | Direction | When | Notes |
|---|---|---|---|
| `commit_intake` + `HERMES_WRITE_TO_ZOHO=1` | Supabase opportunities → Zoho Account/Contacts/Deals | On intake approval | Zoho failure is non-fatal; Supabase rows stay |
| `scripts/backfill_zoho_from_momentum.py` | NowCerts → Zoho (+ Nextcloud folders) | One-shot / migration | Not scheduled |

Zoho IDs are stamped on `opportunities` (`zoho_account_id`, etc.) when writes succeed.

---

## Target sync flows (to build)

### A. NowCerts → Supabase → Zoho (policies & insured facts)

Mirror the existing book-sync discipline:

1. **Fetch** changed insureds/policies from NowCerts (watermark in `sync_state`).
2. **Upsert** `canonical_clients` / `canonical_policies` (unchanged).
3. **Upsert** Zoho `Accounts`, custom **Policies**, **Renewals** modules using
   external IDs (`nowcerts_insured_guid`, policy GUID) — see `docs/zoho/`.
4. **Audit** in `sync_audit_log`; conflicts in `sync_conflicts`.

**Direction:** AMS → Supabase → Zoho. Zoho does not invent insured/policy truth.

**Cadence proposal:**

| Feed | Cadence | Rationale |
|---|---|---|
| Canonical book (Supabase) | Daily incremental (existing) | Cockpit + agent read latency |
| Zoho policy/account upsert | Daily after book sync (or chained in same run) | CRM view lags AMS ≤ 24h |
| Zoho renewals upsert | Daily 2:35am ET after `--renewal-refresh` (`hermes --sync-zoho-renewals`) | Creator Renewals Desk |
| Zoho AMS_Write_Queue mirror | Daily 2:40am ET (`hermes --sync-zoho-ams-queue`) | Approved Creator jobs → executor |
| Live AMS reads | On demand (`ams_client_snapshot`) | When user needs "right now" |

### B. Zoho → Supabase (pipeline mirror, read path)

For Hermes agent tools and Command Center dashboards that still read Supabase:

1. Poll Zoho `Deals` / case modules by `Modified_Time` > cursor.
2. Stage raw payloads in `inbound_sync_staging` (or a dedicated `zoho_sync_staging`).
3. Upsert `opportunities` and (during migration) replace `agency_crm_*` reads.

**Direction:** Zoho → Supabase for **read mirrors only**. Writes to CRM go to Zoho first.

**Cadence proposal:** every 15–60 minutes for pipeline/case mirrors, or webhook-driven when Zoho notifications are enabled.

### C. Hermes writes (human-approved)

Unchanged principle from the Operating Constitution:

```
Human approval → outbound_sync_queue (or Zoho API for CRM-native writes)
    → execute → re-read destination → receipt / audit row
```

CRM writes land in **Zoho**; AMS writes land in **NowCerts** via the existing queue.

---

## Conflict handling

| Scenario | Rule |
|---|---|
| AMS vs canonical book | **NowCerts wins** for policy facts; repair mirror (`--sync-canonical-book`) |
| Zoho vs NowCerts on premium/dates | **NowCerts wins**; Zoho fields updated from book sync |
| Zoho vs Supabase `opportunities` on stage/amount | **Zoho wins** for CRM pipeline; Supabase row is mirror |
| Duplicate Account in Zoho | External ID on `nowcerts_insured_guid`; search-before-create in `zoho_client` |
| Unmapped NowCerts insured | Skip policy upsert; log to `sync_conflicts` / report (no silent Account create) |
| Concurrent human edit in Zoho | Last-write-wins on mirror; audit `Modified_Time`; never overwrite without re-read |

---

## Freshness signals (operator-facing)

| Signal | Endpoint / field | Meaning |
|---|---|---|
| AMS queue freshness | `GET /api/hermes/sync-health` → `latest_completed` | Last completed `outbound_sync_queue` job to NowCerts |
| Book mirror drift | `GET /api/hermes/book-sync` | NowCerts vs `canonical_policies` agreement |
| Zoho row freshness | Zoho `Last_Synced` / `Last_AMS_Sync` on Policies & Deals | Per-record stamp from sync job (see `docs/zoho/fields_*.csv`) |
| Intake Zoho mirror | `opportunities.zoho_*_id` populated | Intake commit reached Zoho when flag on |

Dashboard rule: show **worst-of** book-sync OK + queue depth + Zoho `Last_Synced` age for the entity type being displayed.

---

## Migration: `agency_crm_*` → Zoho

1. **Read path:** agent tools (`crm_client_activity`, `list_cases`) still hit Supabase legacy tables; labels in `docs/hermes-tool-map.md` mark these as legacy → Zoho.
2. **Write path:** new cases/tasks created in Zoho (or via Hermes → Zoho client), not new rows in `agency_crm_*`.
3. **Backfill:** `backfill_zoho_from_momentum.py` for Accounts/Policies/Renewals; optional one-shot case import TBD.
4. **Decommission:** when Zoho case module is live and mirrors stable, stop writing `agency_crm_*` and repoint read tools to Zoho APIs.

---

## Related code & docs

| Area | Location |
|---|---|
| Zoho REST client | `packages/rsg-hermes-core/hermes_integrations/zoho_client.py` |
| Intake → Zoho | `hermes/intake/commit.py` (`HERMES_WRITE_TO_ZOHO`) |
| Momentum → Zoho backfill | `scripts/backfill_zoho_from_momentum.py` |
| Zoho renewals upsert | `hermes/sync/zoho_renewals.py` (`hermes --sync-zoho-renewals`) |
| Zoho AMS queue mirror | `hermes/sync/zoho_ams_queue.py` (`hermes --sync-zoho-ams-queue`) |
| Creator Renewals Desk | `docs/zoho/creator-renewals-desk/` |
| Canonical book sync | `hermes/sync/canonical_book_sync.py` |
| Outbound AMS queue | `outbound_sync_queue` + scheduler executors |
| Zoho field model | `docs/zoho/` |
| Historical Espo sync contract | `docs/SYNC_FLOW_CONTRACT.md` (shape only) |

---

## Open decisions

- Scheduled Zoho Accounts/Policies sync job name and cron slot (chain after `--sync-canonical-book` vs separate). `--sync-zoho-renewals` / `--sync-zoho-ams-queue` are scheduled after `--renewal-refresh`.
- Zoho webhook vs poll for Deal/case mirrors.
- Whether `opportunities` remains the Hermes pipeline table or becomes a pure mirror with Zoho external IDs only.
- Case module API names in production Zoho org (confirm against Settings → APIs).
