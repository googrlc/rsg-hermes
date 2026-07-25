# Sync flow contract: AMS / Momentum → Supabase → the CRM (and reverse)

> **HISTORICAL (EspoCRM decommissioned 2026-07-23).** The "CRM" leg of every flow below
> was EspoCRM, and the implementations this contract pointed at —
> `hermes/sync/pipeline.py` and `hermes/sync/bidirectional.py` — have been deleted. The
> `destination_system = 'espocrm'` conventions and `espocrm_id` mappings no longer apply.
>
> **What holds today:** Supabase is still the hub, and the staging/queue/audit discipline
> below is still the rule. The live tables are `inbound_sync_staging` → `canonical_clients` /
> `canonical_policies` (in `hermes/sync/canonical_book_sync.py`) inbound, and
> `outbound_sync_queue` → NowCerts (the approval-gated executors) outbound. Read this for
> the contract shape, not the table names.

## Design principle

**Supabase is the hub.** No production path should write the CRM from n8n without also writing the same intent through Supabase flow tables (staging, queue, audit), except for emergencies.

---

## A. NowCerts / Momentum → Supabase → the CRM

Ordered steps (same run / same `sync_runs.id`):

1. **`sync_runs`** — Insert one row: `workflow_name`, `source_system` (e.g. `momentum` / `nowcerts`), `destination_system` = `espocrm`, `status` = `running`.
2. **`sync_state`** — Read `high_water_mark` (or equivalent cursor) for the source feed.
3. **Fetch** changed records from NowCerts/Momentum since that watermark.
4. **`inbound_sync_staging`** — Upsert raw payloads (`source_object_type`, `source_object_id`, `raw_payload`, `run_id`, `processing_status`).
5. **`sync_mappings`** — Upsert ID links (NowCerts/Momentum entity ↔ future or existing `espocrm_id`).
6. **Business tables** (normalized hub):
   - **`crm_accounts`** — insured / client facts  
   - **`crm_commissions`** — policy / commission facts  
   - **`project_85_renewals`** — expiration / renewal watchlist (`policy_number` unique; use `upsert_renewal` in `hermes/operations/renewal_tracker.py`)  
   - **`agency_snapshots`** — optional book-level aggregates (when that table is populated by your reporting job)
7. **`outbound_sync_queue`** — One row per the CRM write intent: must include **`run_id`**, **`mapping_id`** (nullable), **`attempt_count`** (start at `0`), `object_type`, `action`, `payload`, `status` = `queued`.  
   - Hermes **`pipeline.py`** depends on these columns; drift (e.g. column `attempts` only) breaks enqueue and leaves **`sync_audit_log` / `sync_errors` empty**.  
   - Apply migration: `supabase/migrations/20260511120000_outbound_sync_queue_hermes_alignment.sql`.
8. **Hermes** — Worker drains `outbound_sync_queue` / `crm_write_queue` and applies writes to the CRM.
9. **`sync_audit_log`** — Append success/failure per logical object.
10. **`sync_errors`** — Append retryable/non-retryable failures with `queue_id` / `staging_id` when known.
11. **`sync_conflicts`** — Field-level disagreements or unmapped keys awaiting human resolution.
12. **`sync_state`** — Advance high-water mark **only after** the run is successfully committed (or mark partial with explicit cursor policy).

### n8n Momentum workflow (checklist)

Today many workflows only: read `sync_state` → call the CRM → write `sync_state`. To match this contract, insert nodes **before** the CRM writes:

- Insert row into **`sync_runs`** (capture returned `id`).
- Supabase **insert/upsert** into **`inbound_sync_staging`** with that `run_id`.
- Transform → **`crm_*` / `project_85_renewals`** (HTTP Supabase REST or SQL node with service role).
- Insert **`outbound_sync_queue`** rows (do not call the CRM directly for those records).
- After Hermes processes the queue (or on a schedule), read **`sync_audit_log`** / **`sync_errors`** for alerting.

Defer the CRM HTTP nodes until the queue path is stable, or keep them behind a feature flag.

---

## B. the CRM → Supabase (mirror)

Hermes entrypoint: **`run_crm_to_hub`** in `hermes/sync/bidirectional.py`.

Contract:

1. **`sync_runs`** — `workflow_name` = `crm_to_hub`, `source_system` = `espocrm`, `destination_system` = `supabase`.
2. Poll the CRM **`Account`** / **`Policy`** (or webhooks → staging only) by `modifiedAt` &gt; cursor.
3. **`inbound_sync_staging`** — Raw the CRM payloads for the run (`source_system` = `espocrm`).
4. Upsert **`crm_accounts`** / **`crm_commissions`** (existing mappers in `field_mapper.py`).
5. **`project_85_renewals`** — For policies with `policy_number` + `expiration_date`, upsert watchlist rows for the dashboard.
6. **`sync_audit_log`** — Already written per mirrored account; extend as needed for policies.
7. **`sync_mappings`** — Update when a deterministic NowCerts/Momentum key exists (unique index is on the NowCerts side; do not invent fake NowCerts IDs in production—use staging + `sync_conflicts` until linked).

---

## Related files

| Area | Location |
|------|----------|
| NowCerts → the CRM pipeline | `hermes/sync/pipeline.py` |
| the CRM → Supabase mirror | `hermes/sync/bidirectional.py` |
| Outbound queue schema (canonical) | `supabase/migrations/20260507010000_sync_control_tables.sql` |
| Outbound queue drift fix | `supabase/migrations/20260511120000_outbound_sync_queue_hermes_alignment.sql` |
| Renewal upsert API | `hermes/operations/renewal_tracker.py` |
