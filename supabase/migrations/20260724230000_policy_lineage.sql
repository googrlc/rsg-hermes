-- policy_lineage (2026-07-24) — the one thing canonical_policies holds that the
-- AMS cannot supply.
--
-- Policy FACTS (premium, dates, carrier, status, active) are moving to live
-- NowCerts reads via hermes/ams/book.py. `renewed_policy` cannot move: the
-- NowCerts API exposes no renewal pointer (see canonical_book_sync and
-- candidate_refresh), and eligibility root-walking needs it. It is derived work
-- state rather than an AMS fact, so Supabase stays its owner — this table is
-- what survives when the canonical_* mirrors are retired.
--
-- Idempotent — safe to re-run.

create table if not exists public.policy_lineage (
  policy_guid    text primary key,
  renewed_policy text,
  updated_at     timestamptz not null default now()
);

comment on table public.policy_lineage is
  'policy_guid -> renewed_policy. Renewal lineage the NowCerts API does not expose; '
  'survives the retirement of canonical_policies. Facts come from the live AMS.';

-- Backfill from the mirror while it still exists. canonical_policies stores an
-- EMPTY STRING rather than NULL for "no predecessor" on most rows: 455 are
-- non-null but only 105 carry a real pointer. Filter on btrim, not `is not null`
-- — candidate_refresh._renewed_from() already treats '' as absent, so storing
-- blanks here would just be 350 rows of noise.
insert into public.policy_lineage (policy_guid, renewed_policy)
select policy_guid, btrim(renewed_policy)
  from public.canonical_policies
 where policy_guid is not null
   and coalesce(btrim(renewed_policy), '') <> ''
on conflict (policy_guid) do update
   set renewed_policy = excluded.renewed_policy,
       updated_at     = now();

create index if not exists policy_lineage_renewed_policy_idx
  on public.policy_lineage (renewed_policy);

-- RLS: service_role only, matching the locked-table convention from the
-- 2026-07-23 hardening sweep. No anon/authenticated access.
alter table public.policy_lineage enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
     where schemaname = 'public'
       and tablename  = 'policy_lineage'
       and policyname = 'policy_lineage_service_role'
  ) then
    create policy policy_lineage_service_role on public.policy_lineage
      for all to service_role using (true) with check (true);
  end if;
end $$;

revoke all on public.policy_lineage from anon;
revoke all on public.policy_lineage from authenticated;
