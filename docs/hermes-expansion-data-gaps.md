# Hermes Expansion Data Gap Advisory

This note captures key data-field and table gaps for the Hermes expansion scope (research, underwriting, document/transcript extraction, Medicare, life, commissions, CRM drafting).

## What exists already (live Supabase)

- Core write governance: `crm_write_queue`, `crm_receipts`, `guardrail_logs`
- Intake/document staging: `leads_staging`, `stg_slack_intake_notes`, `stg_underwriting_documents`, `stg_underwriting_requests`, `commercial_documents`, `documents`
- Underwriting and risk: `risk_assessments`, `client_assessments`, `client_reports`, `uw_submission_profile`, `uw_missing_items`, `uw_touchpoint_log`
- Medicare: `medicare_master_plan_index`, `medicare_plans`, `medicare_underwriting_rules`, `medicare_carriers`, and supporting footprint/provider/rx tables
- Life: `life_products`, `life_underwriting_rules`
- Commission: `commission_rules`, `commission_schedule`, `commission_ledger`, `commission_reconciliation`, `commission_audits`

## Key gaps to add or formalize

## 1) Durable evidence log for every research run

- **Missing table:** `research_logs`
- **Why:** Current data is spread across staging/risk tables; there is no single auditable ledger for source-backed research events.
- **Minimum fields to add:**
  - `id`, `account_id`, `opportunity_id`, `policy_id`
  - `research_type`, `input_value`, `summary`
  - `source_links` (JSONB), `confidence_score` (numeric)
  - `missing_data` (JSONB), `risk_flags` (JSONB)
  - `created_at`, `created_by`, `write_status`

## 2) Structured property underwriting facts

- **Gap:** Property facts are currently mixed in `risk_assessments`/JSON blobs.
- **Recommendation:** Create `property_research` (or add strict JSON schema + view over `client_assessments.property_research`).
- **Minimum fields to add:**
  - Address normalization fields: `property_address`, `city`, `state`, `zip`, `county`
  - Parcel/title pre-check fields: `parcel_id`, `owner_name`, `assessor_url`, `recorder_url`, `tax_url`
  - Value/build fields: `land_value`, `building_value`, `total_value`, `year_built`, `square_feet`, `construction_type`, `roof_type`, `occupancy_type`
  - Rebuild and legal-risk fields: `rebuild_low`, `rebuild_high`, `rebuild_confidence`, `title_precheck_flags`, `missing_data`, `last_researched_at`

## 3) Explicit extraction results table for uploaded docs

- **Gap:** `commercial_documents.extraction_json` exists, but no normalized extraction results table for downstream CRM/Supabase updates and confidence-by-field.
- **Recommendation:** Add `document_extractions`.
- **Minimum fields to add:**
  - `id`, `account_id`, `opportunity_id`, `policy_id`
  - `document_name`, `document_type`
  - `extracted_json`, `confidence_json`, `source_pages`
  - `missing_fields`, `risk_flags`, `created_at`

## 4) Transcript-specific summary and action object

- **Gap:** No dedicated transcript summary table with commitments/deadlines/task draft references.
- **Recommendation:** Add `transcript_summaries`.
- **Minimum fields to add:**
  - `id`, `account_id`, `contact_id`, `opportunity_id`
  - `transcript_source`, `summary`
  - `action_items`, `client_commitments`, `rsg_commitments`, `deadlines`
  - `sentiment`, `crm_note_draft`, `created_at`

## 5) First-class underwriting/risk flag record

- **Gap:** Flags are currently array/text fields; hard to route/assign/close reliably.
- **Recommendation:** Add `underwriting_flags`.
- **Minimum fields to add:**
  - `id`, `account_id`, `policy_id`, `opportunity_id`
  - `flag_type`, `severity`, `description`
  - `source`, `recommended_action`
  - `status`, `assigned_to`, `created_at`

## 6) Need-from-client checklist objects

- **Gap:** Missing item tracking exists (`uw_missing_items`) but is underwriting-centric and not broad enough for property/Medicare/life/document flows.
- **Recommendation:** Add `client_need_checklists` or extend `uw_missing_items` with `checklist_type` and broader linkage.
- **Minimum fields to add:**
  - `id`, `account_id`, `opportunity_id`, `policy_id`
  - `checklist_type`, `item`, `reason_needed`
  - `requested_from_client`, `received`, `due_date`, `assigned_to`, `created_at`

## Key field-level cleanup needed (high impact)

- **Life and Medicare field normalization:** tables currently include multiple synonym columns (`carrier`/`company`/`carrier_name`, `line_of_business`/`lob`, etc.). Create canonical views for Hermes reads so extraction and matching stay deterministic.
- **Confidence and source consistency:** ensure all extraction/research artifacts include both `confidence` and source pointers (`source_links` and/or page anchors).
- **CRM linkage consistency:** ensure every new object can resolve to the canonical keys — `nowcerts_insured_guid` (client), `policy_number` / `policy_guid` (policy), and the `agency_crm_cases.id` / `opportunities.id` surrogate keys. The old `espocrm_*_id` columns are dead; do not add new ones.

## Confirm-before-write implications

- Keep writes routed through draft payload objects first.
- Persist draft payload snapshots and approval outcomes in `research_logs` (or equivalent audit table) for traceability.
- Reject write attempts without explicit approval tokens.
