-- Commission surface, Phase 2 groundwork.
-- ⚠ ALREADY APPLIED to prod via Supabase MCP (migration "commission_surface_phase2").
-- This file backfills the repo so migrations stay the single source of truth.
--
-- Preconditions verified before applying:
--   reconciliation_status null rows        0   (CHECK is safe)
--   distinct statuses present              7   (all in the allowlist below)
--   commission_ledger.espocrm_opportunity_id  0 rows of data
--   commission_ledger.espocrm_policy_id      12 rows — ALREADY archived in
--                                            espo_column_archive on 2026-07-24
--   duplicate transaction lines            0   (unique index builds clean)

-- 1. Where a ledger row came from. 'statement' rows are created by ingest for
--    policies the 2026 seeding floor deliberately skips: the floor governs
--    proactive seeding, but money that actually arrived always lands. Without
--    this, a statement-created row is indistinguishable from a seeded one and
--    the floor's exclusion count stops meaning anything.
alter table commission_ledger
    add column if not exists origin text not null default 'seed';

do $$ begin
    alter table commission_ledger add constraint commission_ledger_origin_check
        check (origin in ('seed', 'statement', 'manual'));
exception when duplicate_object then null; end $$;

-- 2. reconciliation_status was FREE TEXT on money data — a typo was a silent
--    bug that would quietly drop a row out of every status filter. Pin it,
--    including the new terminal state 'reconciled', which nothing has ever
--    written; that absence is precisely why the cockpit view rendered empty
--    against its own default filter.
do $$ begin
    alter table commission_ledger add constraint commission_ledger_status_check
        check (reconciliation_status in (
            'pending', 'reconciled', 'underpaid', 'overpaid',
            'no_expected', 'rolled_up', 'canceled', 'missing_statement'));
exception when duplicate_object then null; end $$;

-- 3. Stop a statement line committing twice. commission_ingest_batches already
--    has UNIQUE(content_hash), which guards a whole file being re-uploaded;
--    this guards the same line arriving through two different batches.
create unique index if not exists commission_transactions_line_uq
    on commission_transactions (statement_id, policy_number, transaction_code,
                                transaction_date, commission_amount);

-- 4. Slack is retired; these three columns name a dead system. Rename rather
--    than drop — 3 rows still carry values.
do $$ begin
    alter table commission_ingest_batches rename column slack_channel to source_channel;
exception when undefined_column then null; end $$;
do $$ begin
    alter table commission_ingest_batches rename column slack_file_id to source_file_id;
exception when undefined_column then null; end $$;
do $$ begin
    alter table commission_ingest_batches rename column slack_message_ts to source_ref;
exception when undefined_column then null; end $$;

-- 5. Espo is decommissioned. Both columns' values are preserved in
--    espo_column_archive; the 2026-07-25 purge missed these two.
alter table commission_ledger drop column if exists espocrm_opportunity_id;
alter table commission_ledger drop column if exists espocrm_policy_id;

comment on column commission_ledger.origin is
    'seed = proactively seeded from the book within the commission floor; '
    'statement = created by statement ingest because real money arrived for a '
    'policy outside the floor; manual = hand-created.';

-- Post-apply verification (2026-07-26):
--   origin column present, 108 existing rows defaulted to 'seed'
--   0 espocrm* columns remain on commission_ledger
--   commission_ingest_batches now has source_channel / source_file_id / source_ref
--   commission_transactions_line_uq built
--   both CHECK constraints active; an UPDATE to 'reconcilled' (typo) was
--   rejected with check_violation and rolled back, nothing written
