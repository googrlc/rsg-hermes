-- Agent attribution (multi-instance Hermes, approved 2026-06-11).
-- Two Hermes containers run from one repo (Lamar's and Gretchen's), differentiated
-- only by env (HERMES_AGENT_ID). Every write path stamps that id so actions are
-- attributable and the two instances never get confused in shared tables.
--
-- Neutral default 'hermes' matches hermes.core.identity.DEFAULT_AGENT_ID, so
-- pre-existing rows and an unconfigured container agree. Each real deployment sets
-- HERMES_AGENT_ID explicitly ('hermes-lamar' | 'hermes-gretch').

alter table public.crm_write_queue
  add column if not exists agent_id text not null default 'hermes';

alter table public.agency_intake_drafts
  add column if not exists agent_id text not null default 'hermes';

alter table public.cc_submissions
  add column if not exists agent_id text not null default 'hermes';

create index if not exists idx_crm_queue_agent on public.crm_write_queue(agent_id);
create index if not exists idx_intake_drafts_agent on public.agency_intake_drafts(agent_id);
create index if not exists idx_cc_sub_agent on public.cc_submissions(agent_id);
