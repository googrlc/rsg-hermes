-- carrier-appetite: feedback loop + class-code bridge + evidence-based backfills
--
-- Context: carrier_appetite had no way to (a) learn from placement outcomes and
-- (b) reach the 4,400+ populated rows in the classification tables. This migration
-- adds both, plus the backfills that are defensible from data already on the rows.
--
-- HARD RULE observed throughout: nothing here invents carrier appetite. Every
-- seeded class-code link is a code literally written in the carrier's own source
-- (carrier_appetite.details / notes). Derived links are possible via the
-- match_method column but are NOT seeded here.
--
-- Idempotent and safe to re-run.
--
-- STATUS: fully APPLIED to project rsg-infrastructure (wibscqhkvpijzqbhjphg)
-- on 2026-07-25. This file is the replayable record of what is live.

-- ---------------------------------------------------------------------------
-- 1. Feedback loop
-- ---------------------------------------------------------------------------

create table if not exists public.appetite_placement_outcomes (
  id uuid primary key default gen_random_uuid(),
  account_name        text not null,
  lob                 text not null,
  state               text,
  carrier_recommended text,
  carrier_submitted   text,
  carrier_bound       text,
  appetite_row_id     uuid references public.carrier_appetite(id) on delete set null,
  outcome             text not null check (outcome in
                        ('bound','declined','quoted_not_bound',
                         'no_market','client_withdrew')),
  decline_reason      text,
  premium_bound       numeric,
  recommended_rank    integer,
  recorded_by         text,
  created_at          timestamptz not null default now()
);

create index if not exists appetite_placement_outcomes_lob_state_idx
  on public.appetite_placement_outcomes (lob, state);
create index if not exists appetite_placement_outcomes_carrier_bound_idx
  on public.appetite_placement_outcomes (carrier_bound);
-- supports the "2+ declines, same carrier, same LOB, within 90 days" read rule
create index if not exists appetite_placement_outcomes_decline_pattern_idx
  on public.appetite_placement_outcomes (carrier_submitted, lob, created_at desc);
create index if not exists appetite_placement_outcomes_appetite_row_idx
  on public.appetite_placement_outcomes (appetite_row_id);

alter table public.appetite_placement_outcomes enable row level security;
drop policy if exists appetite_placement_outcomes_service_role
  on public.appetite_placement_outcomes;
create policy appetite_placement_outcomes_service_role
  on public.appetite_placement_outcomes
  for all to service_role using (true) with check (true);

-- ---------------------------------------------------------------------------
-- 2. The class-code bridge
-- ---------------------------------------------------------------------------
-- Why a join table instead of filling carrier_appetite.class_codes[]:
--   * a code can be eligible / conditional / prohibited - an array cannot say which
--   * a code can be scoped to one state (ISC prohibits WC 5552/5553 in CA only)
--   * some carrier codes have NO local equivalent (CNA Connect 87210/51992/80490
--     are not in gl_class_codes) - the raw code must survive even unresolved
--   * derived links must be labelled as derived, never mistaken for carrier truth

create table if not exists public.carrier_appetite_class_codes (
  id           uuid primary key default gen_random_uuid(),
  appetite_id  uuid not null references public.carrier_appetite(id) on delete cascade,
  code_system  text not null check (code_system in ('gl','wc','naics','sic','carrier')),
  code         text not null,
  gl_code_id   uuid references public.gl_class_codes(id) on delete set null,
  wc_code_id   uuid references public.wc_class_codes(id) on delete set null,
  naics_id     uuid references public.naics_codes(id)    on delete set null,
  sic_id       uuid references public.sic_codes(id)      on delete set null,
  eligibility  text not null check (eligibility in ('eligible','conditional','prohibited')),
  match_method text not null check (match_method in ('explicit_source','manual','keyword','embedding')),
  confidence   text not null default 'unverified' check (confidence in ('verified','unverified')),
  state_scope  text,
  restrictions text,
  source_note  text,
  updated_by   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create unique index if not exists cacc_unique_link_idx
  on public.carrier_appetite_class_codes
     (appetite_id, code_system, code, coalesce(state_scope, ''));
create index if not exists cacc_appetite_idx
  on public.carrier_appetite_class_codes (appetite_id);
create index if not exists cacc_code_lookup_idx
  on public.carrier_appetite_class_codes (code_system, code);
create index if not exists cacc_eligibility_idx
  on public.carrier_appetite_class_codes (eligibility);

alter table public.carrier_appetite_class_codes enable row level security;
drop policy if exists cacc_service_role on public.carrier_appetite_class_codes;
create policy cacc_service_role on public.carrier_appetite_class_codes
  for all to service_role using (true) with check (true);

comment on table public.carrier_appetite_class_codes is
  'Bridge between carrier_appetite and the classification tables - the join carrier_appetite.class_codes[] never had. code_system=carrier means a carrier-proprietary code with no local ISO/NCCI equivalent (e.g. CNA Connect). The code column always holds the literal code; the *_code_id FKs resolve only when that code exists in our tables.';
comment on column public.carrier_appetite_class_codes.match_method is
  'explicit_source = the code is literally written in the carrier source. manual = a human entered it. keyword/embedding = machine-derived; annotation and tiebreaker only, never a filter, never presented as carrier-verified appetite.';
comment on column public.carrier_appetite_class_codes.state_scope is
  'Null = applies across the appetite row states. Set when the carrier scopes a code to/out of specific states.';

-- ---------------------------------------------------------------------------
-- 3. Seed - EXPLICIT SOURCE ONLY
-- ---------------------------------------------------------------------------
-- Every row below quotes a code that appears verbatim in carrier_appetite.details.
-- Nothing is inferred. 7 links total; that is genuinely all the explicit
-- code-level evidence currently in the table.

-- CNA "Business Policy" (CNA Connect class eligibility, folded from appetite_records).
-- These are CNA Connect codes, NOT ISO GL codes - verified absent from gl_class_codes.
insert into public.carrier_appetite_class_codes
  (appetite_id, code_system, code, eligibility, match_method, confidence, restrictions, source_note, updated_by)
values
  ('ab061191-1e35-4f65-812d-6e9b16d4ddc2','carrier','87210','eligible','explicit_source','verified',
   'Ineligible: investment brokers, staffing/placement agencies. Prohibited states: AK, HI.',
   'Accounting, Auditing/Bookkeeping - details.cna_connect_class_eligibility','migration:class_bridge'),
  ('ab061191-1e35-4f65-812d-6e9b16d4ddc2','carrier','51992','conditional','explicit_source','verified',
   'Ineligible if direct imports exceed 25% of sales. Sprinkler required if building + BPP > 2M. Prohibited states: AK, HI.',
   'Wood Carvings Distributor - details.cna_connect_class_eligibility','migration:class_bridge'),
  ('ab061191-1e35-4f65-812d-6e9b16d4ddc2','carrier','80490','conditional','explicit_source','verified',
   'Ineligible: home healthcare, overnight care, birth centers. Prohibited states: AK, HI.',
   'Acupuncturists - details.cna_connect_class_eligibility','migration:class_bridge')
on conflict do nothing;

-- ISC Workers Comp - Ghost Policy: "No Roofing 5552/5553 in CA (5551 eligible in other states)"
insert into public.carrier_appetite_class_codes
  (appetite_id, code_system, code, wc_code_id, eligibility, match_method, confidence, state_scope, restrictions, source_note, updated_by)
values
  ('f007c758-a42e-4a2d-abef-f3aedc6f2d4d','wc','5551',
   (select id from public.wc_class_codes where wc_code='5551' limit 1),
   'conditional','explicit_source','verified', null,
   'Eligible in states other than CA.',
   'details.excluded_risks - "No Roofing 5552/5553 in CA (5551 eligible in other states)"','migration:class_bridge'),
  ('f007c758-a42e-4a2d-abef-f3aedc6f2d4d','wc','5552',
   (select id from public.wc_class_codes where wc_code='5552' limit 1),
   'prohibited','explicit_source','verified','CA',
   'Roofing 5552 not written in CA.',
   'details.excluded_risks','migration:class_bridge'),
  ('f007c758-a42e-4a2d-abef-f3aedc6f2d4d','wc','5553',
   (select id from public.wc_class_codes where wc_code='5553' limit 1),
   'prohibited','explicit_source','verified','CA',
   'Roofing 5553 not written in CA.',
   'details.excluded_risks','migration:class_bridge')
on conflict do nothing;

-- ISC Workers Comp: excluded_risks includes "5606 (construction supervisor) standalone"
insert into public.carrier_appetite_class_codes
  (appetite_id, code_system, code, wc_code_id, eligibility, match_method, confidence, restrictions, source_note, updated_by)
values
  ('981c7abf-97b1-4192-a78c-db1385ee5409','wc','5606',
   (select id from public.wc_class_codes where wc_code='5606' limit 1),
   'prohibited','explicit_source','verified',
   'Standalone 5606 (construction supervisor) not eligible.',
   'details.excluded_risks','migration:class_bridge')
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- 4. Resolver views
-- ---------------------------------------------------------------------------

create or replace view public.vw_carrier_appetite_class_resolved as
select
  b.id                as link_id,
  ca.id               as appetite_id,
  ca.carrier_name,
  ca.carrier_id,
  ca.lob,
  ca.appetite_level,
  ca.confidence       as appetite_confidence,
  ca.states_approved,
  ca.active,
  b.code_system,
  b.code,
  coalesce(g.description, w.description, n.naics_title, s.sic_description) as code_description,
  b.eligibility,
  b.match_method,
  b.confidence        as link_confidence,
  b.state_scope,
  b.restrictions,
  b.source_note,
  (b.gl_code_id is not null or b.wc_code_id is not null
   or b.naics_id is not null or b.sic_id is not null) as resolves_locally,
  ca.updated_at       as appetite_updated_at
from public.carrier_appetite_class_codes b
join public.carrier_appetite ca on ca.id = b.appetite_id
left join public.gl_class_codes g on g.id = b.gl_code_id
left join public.wc_class_codes w on w.id = b.wc_code_id
left join public.naics_codes    n on n.id = b.naics_id
left join public.sic_codes      s on s.id = b.sic_id;

comment on view public.vw_carrier_appetite_class_resolved is
  'carrier_appetite class-code links with descriptions resolved. resolves_locally=false means the carrier uses a proprietary code we have no local definition for - still valid appetite, just not joinable to the classification tables.';

-- "Who writes NAICS <x>?" - the end-to-end path that was previously impossible.
-- NAICS -> GL/WC via the existing mapping tables -> bridge -> appetite -> carrier.
create or replace view public.vw_who_writes_naics as
with naics_expanded as (
  select n.naics_code, n.naics_title, 'gl'::text as code_system, g.gl_code as code
    from public.naics_codes n
    join public.naics_gl_mappings ngl on ngl.naics_id = n.id
    join public.gl_class_codes g on g.id = ngl.gl_code_id
  union all
  select n.naics_code, n.naics_title, 'wc'::text, w.wc_code
    from public.naics_codes n
    join public.naics_wc_mappings nwc on nwc.naics_id = n.id
    join public.wc_class_codes w on w.id = nwc.wc_code_id
)
select
  e.naics_code,
  e.naics_title,
  r.carrier_name,
  r.carrier_id,
  r.lob,
  r.appetite_level,
  r.appetite_confidence,
  r.states_approved,
  r.code_system,
  r.code             as matched_code,
  r.code_description as matched_code_description,
  r.eligibility,
  r.match_method,
  r.state_scope,
  r.restrictions
from naics_expanded e
join public.vw_carrier_appetite_class_resolved r
  on r.code_system = e.code_system and r.code = e.code
where r.active is true;

comment on view public.vw_who_writes_naics is
  'End-to-end NAICS -> carrier path via naics_gl_mappings / naics_wc_mappings. READ THE eligibility COLUMN: this view returns prohibited and conditional links too, not only eligible ones - the name describes the join, not the verdict. An empty result means "no code-level link on file", NOT "nobody writes it"; fall back to LOB-level matching in the carrier-appetite skill. Coverage is bounded by the mapping tables (~6% of NAICS) and by how many bridge links exist.';

-- ---------------------------------------------------------------------------
-- 5. carrier_id backfill - unambiguous roster matches only
-- ---------------------------------------------------------------------------
-- NOTE: carrier_appetite_carrier_lob_uq is UNIQUE (carrier_id, lob) WHERE
-- carrier_id IS NOT NULL. Progressive sub-brands (Mountain, Freedom) therefore
-- CANNOT share the parent's roster id on the same LOB. They are left null on
-- purpose - the fix is to add them to `carriers`, not to collapse them. See §7.

update public.carrier_appetite set carrier_id='three-insurance',
       updated_by='carrier-appetite-backfill', updated_at=now()
 where carrier_id is null and carrier_name='THREE BY BERKSHIRE HATHAWAY';

update public.carrier_appetite set carrier_id='progressive-commercial',
       updated_by='carrier-appetite-backfill', updated_at=now()
 where carrier_id is null and carrier_name='PROGRESSIVE' and lob='Commercial Auto';

update public.carrier_appetite set carrier_id='progressive',
       updated_by='carrier-appetite-backfill', updated_at=now()
 where carrier_id is null and carrier_name='PROGRESSIVE' and lob in ('Personal Auto','Homeowners');

-- ---------------------------------------------------------------------------
-- 6. states_approved - only where the row's OWN source states the territory
-- ---------------------------------------------------------------------------
-- 14 of the 16 blank rows say, verbatim, "US - see program map (states not
-- itemized)" or "program-designated states". Those are NOT populated here.
-- Guessing them would manufacture licensure claims. Only the two ISC trucking
-- rows carry an explicit territory: "48 contiguous states".

update public.carrier_appetite
   set states_approved = array[
        'AL','AZ','AR','CA','CO','CT','DE','FL','GA','ID','IL','IN','IA','KS',
        'KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
        'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT',
        'VT','VA','WA','WV','WI','WY'],
       updated_by='carrier-appetite-backfill', updated_at=now()
 where id in ('b31f654d-8e1d-4204-b7b6-30f0ea3315d5',  -- ISC Trucking - Cargo
              'f4ef67ce-3d18-4bf3-ab62-f49c92a51b54')  -- ISC Trucking - Physical Damage
   and (states_approved is null or cardinality(states_approved)=0);

-- ---------------------------------------------------------------------------
-- 7. Log the gaps that need a human, so they surface instead of evaporating
-- ---------------------------------------------------------------------------

insert into public.data_quality_issues (domain, severity, issue_type, issue_detail, owner, resolution_status)
select 'carrier_appetite','medium','missing_carrier_appointment',
       'carrier_appetite references carrier "'||x.carrier_name||'" ('||x.n||' row(s)) with no matching row in `carriers`. This is a missing appointment-roster entry, not a broken join - the underwriter-contact path stays dead until the carrier is added to `carriers`.',
       'lamar','open'
from (
  select ca.carrier_name, count(*) n
    from public.carrier_appetite ca
   where ca.carrier_id is null
     and not exists (select 1 from public.carriers c
                      where upper(trim(c.name)) = upper(trim(ca.carrier_name)))
   group by ca.carrier_name
) x
where not exists (
  select 1 from public.data_quality_issues d
   where d.domain='carrier_appetite'
     and d.issue_type='missing_carrier_appointment'
     and d.issue_detail like '%"'||x.carrier_name||'"%'
);

insert into public.data_quality_issues (domain, severity, issue_type, issue_detail, owner, resolution_status)
select 'carrier_appetite','medium','states_approved_not_itemized',
       'carrier_appetite row '||ca.id||' ('||ca.carrier_name||' / '||ca.lob||
       ') has no states_approved. Its own source says the territory is not itemized, so it cannot be derived from data on hand - it needs the carrier program map. Until then the row is state_unconfirmed and must never be treated as nationwide.',
       'lamar','open'
from public.carrier_appetite ca
where (ca.states_approved is null or cardinality(ca.states_approved)=0)
  and not exists (
    select 1 from public.data_quality_issues d
     where d.domain='carrier_appetite'
       and d.issue_type='states_approved_not_itemized'
       and d.issue_detail like '%'||ca.id::text||'%'
  );

-- ---------------------------------------------------------------------------
-- 8. NOT DONE ON PURPOSE - needs a human decision
-- ---------------------------------------------------------------------------
-- a) Adding the ~14 missing carriers to `carriers` (ALLSTATE, EMPLOYERS, RLI,
--    PATHPOINT, NEPTUNE, SEMSEE, KELLY KLEE, SLICE, ANNEX RISK, EVERPEAK,
--    QBE Specialty, SIMPLICITY, SES RISK SOLUTIONS, STATE NATL, GEICO MARINE,
--    PROGRESSIVE MOUNTAIN, PROGRESSIVE FREEDOM). Creating an appointment record
--    asserts a business relationship - Lamar confirms, then the carrier_id
--    backfill finishes itself.
-- b) appetite_level on the 16 null rows. NB: the live CHECK constraint allows
--    'declined' as a 4th value, which the carrier-appetite skill does not
--    document. Reconcile before bulk-setting anything.
