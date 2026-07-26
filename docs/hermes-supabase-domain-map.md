# Hermes Supabase Domain Map

Domain mapping based on live `public` schema inventory.

## CRM governance

- `outbound_sync_queue`: staged outbound CRM/AMS writes, gated on `approved_by` + `approved_at`
- `renewal_execution_receipts`: per-job before/after proof for renewal writes
- `guardrail_logs`: guardrail decisions
- `sync_audit_log`: sync history

> Corrected 2026-07-26: this section previously listed `crm_write_queue` and
> `crm_receipts`. Neither table exists in the live schema — they were designed
> for the Espo-era write path and never built.

## Intake and document flow

- `leads_staging`
- `stg_slack_intake_notes`
- `documents`
- `commercial_documents`
- `ingestion_event_log`
- `review_queue`
- `stg_underwriting_documents`
- `stg_underwriting_requests`

## Underwriting and risk

- `risk_assessments`
- `client_assessments`
- `client_reports`
- `uw_submission_profile`
- `uw_missing_items`
- `uw_touchpoint_log`
- `carrier_appetite`
- `appetite_carrier_profiles`

## Medicare

- `medicare_master_plan_index`
- `medicare_plans`
- `medicare_carriers`
- `medicare_underwriting_rules`
- `medicare_county_footprints`
- `medicare_provider_registry`
- `medicare_medical_rx_matrix`

## Life

- `life_products`
- `life_underwriting_rules`

## Commission

- `commission_rules`
- `commission_schedule`
- `commission_ledger`
- `commission_reconciliation`
- `commission_audits`

## Reuse-first recommendations

- Reuse `uw_missing_items` for underwriting checklist status where possible.
- Reuse `commercial_documents` + `documents` as raw extraction source artifacts.
- Reuse `review_queue` for human escalation queue.

## Add-if-missing canonical objects

- `research_logs`
- `property_research`
- `document_extractions`
- `transcript_summaries`
- `underwriting_flags`
- `client_need_checklists`

