-- One canonical way to name a human, and a service account that says so.
--
-- Three formats were in use for the same concept:
--   opportunities.assigned_to        text holding a NowCerts array: '["Lamar Coates"]'
--   agency_crm_tasks.assigned_to_email  email, real FK to agency_crm_users
--   agency_crm_cases.owner_email        email, real FK
--
-- The display-name form is not just inconvenient, it was UNRESOLVABLE:
-- "Lamar Coates" mapped to BOTH lamar@risksolutionsgroup.net and
-- lc-rsg@risksolutionsgroup.net, so display_name -> email was not a function and
-- the 8 opportunities holding '["Lamar Coates"]' could not be backfilled without
-- guessing which of two accounts owns the deal.
--
-- lc-rsg@ is a service account. The evidence is in the usage: it is created_by on
-- 5 tasks and assigned_to on ZERO, and it is the silent shared login behind the
-- commission tracker. It was simply wearing Lamar's display name. Naming it
-- honestly makes display_name unique, which makes the backfill safe.
--
-- It also gives _service_email() a real home. That helper defaulted to
-- lamar@risksolutionsgroup.net with a comment apologising that no bot user
-- existed — so every machine write was attributed to Lamar personally.

-- 1. 'service' is a real role. The check constraint didn't allow it.
alter table public.agency_crm_users
  drop constraint if exists agency_crm_users_role_check;
alter table public.agency_crm_users
  add constraint agency_crm_users_role_check
  check (role = any (array[
    'administrator', 'manager', 'producer', 'csr', 'read_only', 'service'
  ]));

-- 2. Name the service account for what it is.
update public.agency_crm_users
   set display_name = 'RSG Service',
       role         = 'service'
 where email = 'lc-rsg@risksolutionsgroup.net';

-- Display names must stay unique, or the resolution below silently breaks again.
create unique index if not exists agency_crm_users_display_name_uq
  on public.agency_crm_users (display_name);

-- 3. The canonical reference on opportunities: an email FK, like everywhere else.
alter table public.opportunities
  add column if not exists assigned_to_email text
  references public.agency_crm_users(email);

comment on column public.opportunities.assigned_to_email is
  'Canonical owner: FK to agency_crm_users. The legacy assigned_to column holds '
  'the NowCerts display-name array and is kept only so the AMS mirror round-trips.';
comment on column public.opportunities.assigned_to is
  'LEGACY / AMS mirror. NowCerts-shaped display-name array (''["Lamar Coates"]''). '
  'Read assigned_to_email instead; this exists so writeback can round-trip.';

-- 4. Backfill. Safe now that display_name is unique — resolved by join, not by a
--    hardcoded guess, so it stays correct if a name changes.
update public.opportunities o
   set assigned_to_email = u.email
  from public.agency_crm_users u
 where o.assigned_to is not null
   and o.assigned_to_email is null
   and btrim(o.assigned_to, '[]"') = u.display_name;

-- 5. Tia Coates has no agency_crm_users row and is not a current CRM user — the
--    value arrived from the NowCerts mirror. Her one opportunity is Homeowners,
--    which is personal lines, so it goes to Gretchen per the ownership rule.
--    The legacy display string is cleared: leaving it would keep reproducing an
--    unresolvable assignment on every future backfill.
update public.opportunities
   set assigned_to_email = 'gretchen@risksolutionsgroup.net',
       assigned_to       = '["Gretchen Coates"]'
 where assigned_to = '["Tia Coates"]';

create index if not exists opportunities_assigned_to_email_idx
  on public.opportunities (assigned_to_email)
  where assigned_to_email is not null;
