-- Standalone tasks — work that isn't a client case (issue #195).
--
-- agency_crm_tasks.case_id was NOT NULL with FK -> agency_crm_cases ON DELETE
-- CASCADE, so every task had to hang off a case. All 14 cases are client work
-- (renewal / marketing / service, every one with an insured), which left no way
-- to record internal follow-up: "update commission percentage", "fix the rate
-- sheet", "chase the carrier appointment paperwork".
--
-- The only way to file one of those today is to bolt it onto some client's case.
-- That is wrong on its face, and the CASCADE makes it dangerous: deleting that
-- unrelated case silently deletes your internal task with it.
--
-- After this migration a task has three legitimate shapes:
--
--   case-linked      case_id set              -- client work, unchanged
--   client, no case  insured_database_id set  -- "fix Acme's commission rate"
--   purely internal  both NULL                -- "update commission percentage"
--
-- The CASCADE stays correct for case-linked tasks: deleting a case should take
-- its tasks. A standalone task has no case, so it can no longer be caught by it.

alter table public.agency_crm_tasks
  alter column case_id drop not null;

-- Deliberately NO foreign key to canonical_clients. That table is a mirror under
-- a two-writer freeze (rsg-import tombstoned rows as recently as 2026-07-24), and
-- an FK onto a table that gets rewritten would turn a sync defect into a task
-- that cannot be saved. agency_crm_document_links.insured_database_id follows the
-- same convention for the same reason.
alter table public.agency_crm_tasks
  add column if not exists insured_database_id uuid;

comment on column public.agency_crm_tasks.case_id is
  'Parent case when this is client case work. NULL for standalone tasks.';
comment on column public.agency_crm_tasks.insured_database_id is
  'NowCerts insured GUID when a standalone task concerns a client but needs no '
  'case. No FK on purpose: canonical_clients is a mirror that gets rewritten.';

-- Partial indexes: the cockpit asks for "internal tasks" and "this client''s
-- tasks" as distinct lists, and both are sparse against a case-linked majority.
create index if not exists agency_crm_tasks_internal_idx
  on public.agency_crm_tasks (created_at desc)
  where case_id is null;

create index if not exists agency_crm_tasks_insured_idx
  on public.agency_crm_tasks (insured_database_id)
  where insured_database_id is not null;
