-- Drop the dead espo-named columns (2026-07-24 EspoCRM purge).
--
-- NOT YET APPLIED. Run this deliberately — it is destructive.
--
-- Every column below was verified to have ZERO references in hermes/*.py before
-- being listed. The populated ones were archived first by the companion
-- migration 20260724235000_espo_column_backup.sql into public.espo_column_archive
-- (2,570 values), so nothing here is unrecoverable.
--
-- Populated columns being dropped:
--   slack_user_map.espo_user_id   2 rows  (0 code refs — not used for routing,
--                                          despite the name; verified by grep)
-- Everything else listed is empty in prod.
--
-- DELIBERATELY NOT IN THIS FILE — the columns that live code still reads:
--   sync_mappings.espocrm_id / espocrm_entity_type   (1154 / 1301 rows)
--   canonical_clients.espocrm_id                      (101 rows)
--   commission_ledger.espocrm_policy_id               (12 rows, money)
--   sync_conflicts.espocrm_id / espocrm_value
-- Those need RENAME to crm_* landed in the SAME change as their code updates
-- (sync/field_mapper.py, sync/bidirectional.py, jobs/commission_ingest.py,
-- commands/sync.py, sync/pipeline.py). Renaming them ahead of the code would
-- break the sync-conflict read and the commission ingest on the next run.

begin;

-- ---- no code references, empty or archived --------------------------------
alter table public.slack_user_map           drop column if exists espo_user_id;
alter table public.task_notify_audit        drop column if exists espo_user_id;

alter table public.nowcerts_insured_mirror  drop column if exists espocrm_id;
alter table public.nowcerts_insured_mirror  drop column if exists last_espo_sync_at;
alter table public.nowcerts_insured_mirror  drop column if exists raw_espo_payload;

alter table public.policy_change_events     drop column if exists espocrm_policy_id;
alter table public.policy_change_events     drop column if exists espocrm_note_id;
alter table public.policy_change_events     drop column if exists espocrm_note_posted;

alter table public.calendar_events          drop column if exists espocrm_account_id;
alter table public.calendar_events          drop column if exists espocrm_account_name;
alter table public.carrier_contacts         drop column if exists espocrm_contact_id;
alter table public.client_assessments       drop column if exists espocrm_account_id;
alter table public.client_assessments       drop column if exists espocrm_opportunity_id;
alter table public.client_reports           drop column if exists espocrm_account_id;
alter table public.commission_parity_report drop column if exists espocrm_commission_id;
alter table public.commission_parity_report drop column if exists espocrm_policy_id;
alter table public.email_sequence_log       drop column if exists espocrm_opportunity_id;
alter table public.email_sequence_log       drop column if exists espocrm_account_id;
alter table public.leads_staging            drop column if exists espocrm_account_id;
alter table public.leads_staging            drop column if exists espocrm_opportunity_id;
alter table public.quote_prescreens         drop column if exists espocrm_opportunity_id;
alter table public.commission_ledger        drop column if exists espocrm_opportunity_id;

commit;

-- Verify afterwards — should return zero rows:
--   select table_name, column_name from information_schema.columns
--    where table_schema='public' and column_name ilike '%espo%';
