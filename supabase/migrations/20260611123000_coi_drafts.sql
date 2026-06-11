-- COI draft log (ACORD 25 generator, approved 2026-06-11).
-- One row per drafted certificate of insurance. Drafts are never auto-sent;
-- this is the audit trail of what was prepared, by which instance, for whom.

create table if not exists public.coi_drafts (
  id uuid primary key default gen_random_uuid(),
  agent_id text not null default 'hermes',       -- hermes-gretch | hermes-lamar
  account text not null,                          -- insured / client name
  holder text not null,                           -- certificate holder from the request
  output_path text,                               -- local/bucket path of the draft PDF
  drive_url text,                                 -- Google Drive link once uploaded
  placed_fields jsonb default '[]'::jsonb,        -- form fields successfully filled
  skipped_fields jsonb default '[]'::jsonb,       -- fields not found in the template
  auto_sent boolean not null default false,       -- always false — drafts only
  created_at timestamptz default now()
);

create index if not exists idx_coi_drafts_agent on public.coi_drafts(agent_id, created_at);
create index if not exists idx_coi_drafts_account on public.coi_drafts(account);
