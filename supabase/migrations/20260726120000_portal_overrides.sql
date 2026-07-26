-- Portal overrides — human corrections that outrank a synced source, until the
-- source catches up.
--
-- The workflow this exists for: someone spots wrong data in the cockpit, fixes
-- it there so the numbers are right today, and separately corrects NowCerts by
-- hand. Without a record of that, the nightly sync silently reverts the fix and
-- nobody knows why the number moved back.
--
-- An override is self-retiring. It stores what the source said at the time
-- (`original_value`) alongside the correction (`override_value`). On each sync:
--
--   source == override_value  -> the AMS caught up. RETIRE; source wins again.
--   source == original_value  -> unchanged. KEEP overriding.
--   source == something else  -> the AMS moved somewhere unexpected.
--                                CONFLICT — a human decides, we do not guess.
--
-- That third branch is the point. Auto-retiring on any change would throw away
-- a correction the moment a carrier tweaked an unrelated field.
--
-- Keyed by NATURAL key (policy_number), not row id: canonical_policies and
-- commission_ledger rows are rebuilt by their syncs, and an override must
-- survive a re-seed.

create table if not exists portal_overrides (
    id              uuid primary key default gen_random_uuid(),

    entity_type     text not null,      -- 'commission_ledger' | 'canonical_policies' | ...
    entity_key      text not null,      -- natural key, e.g. policy_number
    field_name      text not null,

    original_value  jsonb,              -- what the source said when this was set
    override_value  jsonb not null,     -- what a human says it should be

    status          text not null default 'active'
                    check (status in ('active', 'retired', 'conflicted')),
    reason          text,

    -- Money and book data: every override is a named decision.
    approved_by     text not null,
    approved_at     timestamptz not null default now(),

    retired_at      timestamptz,
    retired_reason  text,               -- 'ams_matched' | 'withdrawn' | 'superseded'
    conflict_value  jsonb,              -- the unexpected source value, when conflicted

    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- One active override per field. Retired and conflicted rows stay for history.
create unique index if not exists portal_overrides_active_uq
    on portal_overrides (entity_type, entity_key, field_name)
    where status = 'active';

create index if not exists portal_overrides_lookup
    on portal_overrides (entity_type, entity_key)
    where status = 'active';

create index if not exists portal_overrides_status
    on portal_overrides (status, entity_type);

comment on table portal_overrides is
    'Human corrections that outrank a synced source until the source matches, '
    'then auto-retire. Keyed by natural key so they survive a re-seed.';
comment on column portal_overrides.original_value is
    'Source value at the time of override — used to detect whether the source '
    'has since moved to the correction, stayed put, or drifted elsewhere.';
comment on column portal_overrides.conflict_value is
    'Set when the source moved to a third value; the override is held, not '
    'retired, until a human resolves it.';

-- Audit: every portal write, before and after. Distinct from portal_overrides,
-- which holds current state — this is the immutable log.
create table if not exists portal_write_log (
    id              uuid primary key default gen_random_uuid(),
    entity_type     text not null,
    entity_key      text not null,
    field_name      text,
    action          text not null,      -- 'override_set' | 'override_retired' | 'reconcile' | ...
    before_value    jsonb,
    after_value     jsonb,
    actor           text not null,
    note            text,
    created_at      timestamptz not null default now()
);

create index if not exists portal_write_log_entity
    on portal_write_log (entity_type, entity_key, created_at desc);

comment on table portal_write_log is
    'Immutable before/after log of every portal-originated write.';

alter table portal_overrides enable row level security;
alter table portal_write_log enable row level security;

-- Service-role only, matching the rest of the money tables. The API writes with
-- the service key; nothing client-side touches these directly.
drop policy if exists portal_overrides_service on portal_overrides;
create policy portal_overrides_service on portal_overrides
    for all to service_role using (true) with check (true);

drop policy if exists portal_write_log_service on portal_write_log;
create policy portal_write_log_service on portal_write_log
    for all to service_role using (true) with check (true);
