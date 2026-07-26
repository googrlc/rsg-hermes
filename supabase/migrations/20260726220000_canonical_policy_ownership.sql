-- One writer per row on the canonical book.
--
-- THE INCIDENT. canonical_policies had two writers and no way to say which one
-- owned a row. `rsg-import` (pg_cron, UTC) pulled only is_quote=false and marked
-- everything absent from that pull as 'Inactive: not in NowCerts'. It therefore
-- tombstoned rows it had never created: of 48 tombstoned rows, 43 belong to the
-- 285 loaded from Export_Policy_UPLOAD_READY.csv, against only 5 of its own 334.
-- rsg-import was disabled 2026-07-24 and the book has been running on one writer
-- since.
--
-- The mitigation to date is a DOWNSTREAM filter: agency_snapshot.py and
-- commissions/surface.py each carry their own copy of the string
-- "Inactive: not in NowCerts" and skip matching rows. That hides the corruption
-- from two consumers. It does not stop it, and it silently omits any consumer
-- that forgets to check.
--
-- This adds the missing concept: sync_owner says who may perform a DESTRUCTIVE
-- write (deactivate / tombstone) on a row. It is distinct from source_file, which
-- records where a row originally came from and never changes. A row can be
-- refreshed by anyone; it can only be killed by its owner.
--
-- PRECONDITION FOR RE-ENABLING rsg-import: its tombstone step must be scoped to
-- `where sync_owner = 'rsg-import'`. Until that is true, re-enabling it
-- reproduces the July corruption exactly — the column alone does not stop a
-- writer that ignores it, it only makes the damage attributable.

alter table public.canonical_policies
  add column if not exists sync_owner text;

comment on column public.canonical_policies.sync_owner is
  'Which writer may DEACTIVATE or tombstone this row. Any writer may refresh '
  'volatile fields; only the owner may kill it. Distinct from source_file, which '
  'is immutable origin. rsg-import must scope its tombstone step to its own rows.';

-- Backfill from the origin we already have. csv-import is the historical bulk
-- load; those rows are exactly the ones rsg-import had no business tombstoning.
update public.canonical_policies
   set sync_owner = case
         when source_file = 'nowcerts:rsg-import' then 'rsg-import'
         else 'csv-import'
       end
 where sync_owner is null;

alter table public.canonical_policies
  alter column sync_owner set default 'book_sync';

create index if not exists canonical_policies_sync_owner_idx
  on public.canonical_policies (sync_owner);

-- Detection, not just prevention: a tombstone applied by a non-owner is the
-- signature of the bug. This view makes a recurrence visible in one query
-- instead of being inferred from a premium number that looks wrong.
create or replace view public.vw_canonical_policy_tombstones as
select
  policy_number,
  policy_guid,
  source_file,
  sync_owner,
  status,
  active,
  sync_owner is distinct from 'rsg-import' as tombstoned_by_non_owner
from public.canonical_policies
where status like 'Inactive: not in NowCerts%';

comment on view public.vw_canonical_policy_tombstones is
  'Tombstoned canonical policies. tombstoned_by_non_owner = true is the two-writer '
  'bug recurring: a writer killed a row it does not own. Expect 43 historical rows '
  'from the 2026-07 rsg-import incident; a rise above that is a new event.';
