-- Archive every populated espo-named column before the purge (2026-07-24).
-- ⚠ ALREADY APPLIED to prod via Supabase MCP (migration "espo_column_backup").
-- This file backfills the repo so migrations stay the single source of truth.
-- Idempotent in shape, but re-running duplicates rows — guard on your side.
--
-- 2,570 values captured:
--   sync_mappings.espocrm_entity_type  1301
--   sync_mappings.espocrm_id           1154
--   canonical_clients.espocrm_id        101
--   commission_ledger.espocrm_policy_id  12
--   slack_user_map.espo_user_id           2
--
-- Drop espo_column_archive once the purge has been live long enough to trust.

create table if not exists public.espo_column_archive (
  id            bigserial primary key,
  source_table  text not null,
  source_column text not null,
  row_key       text,
  value         text,
  archived_at   timestamptz not null default now()
);

insert into public.espo_column_archive (source_table, source_column, row_key, value)
select 'sync_mappings', 'espocrm_id', id::text, espocrm_id
  from public.sync_mappings where espocrm_id is not null;

insert into public.espo_column_archive (source_table, source_column, row_key, value)
select 'sync_mappings', 'espocrm_entity_type', id::text, espocrm_entity_type
  from public.sync_mappings where espocrm_entity_type is not null;

insert into public.espo_column_archive (source_table, source_column, row_key, value)
select 'canonical_clients', 'espocrm_id', nowcerts_insured_guid, espocrm_id
  from public.canonical_clients where espocrm_id is not null;

insert into public.espo_column_archive (source_table, source_column, row_key, value)
select 'commission_ledger', 'espocrm_policy_id', id::text, espocrm_policy_id
  from public.commission_ledger where espocrm_policy_id is not null;

insert into public.espo_column_archive (source_table, source_column, row_key, value)
select 'slack_user_map', 'espo_user_id', id::text, espo_user_id
  from public.slack_user_map where espo_user_id is not null;

alter table public.espo_column_archive enable row level security;
do $$ begin
  if not exists (select 1 from pg_policies where schemaname='public'
                 and tablename='espo_column_archive'
                 and policyname='espo_column_archive_service_role') then
    create policy espo_column_archive_service_role on public.espo_column_archive
      for all to service_role using (true) with check (true);
  end if;
end $$;
revoke all on public.espo_column_archive from anon;
revoke all on public.espo_column_archive from authenticated;
