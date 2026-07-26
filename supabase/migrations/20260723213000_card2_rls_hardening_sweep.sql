-- CARD 2 · RLS hardening sweep (2026-07-23)
-- ⚠ ALREADY APPLIED to prod 2026-07-23 via Supabase MCP (schema_migrations:
-- "card2_rls_hardening_sweep"). This file backfills the repo so migrations
-- remain the single source of truth for ALL agents (Card 9 rule).
-- Idempotent — safe to re-run.
--
-- Scope:
--  1) Revoke anon on 16 SECURITY DEFINER views (they bypassed table RLS)
--  2) Explicit service_role policies on 20 locked (RLS-on/no-policy) tables
--  3) agency_links: anon read-only; writes require allowlist
--  4) agency_tasks / pipeline_issues: allowlist-gate permissive policies
--  5) Allowlist helper fns: revoke anon EXECUTE
--  6) Drop 4 dead OpenClaw functions (queue table dropped 2026-06-25)
--  7) Pin search_path on advisor-flagged functions
-- Accepted residuals (deliberate): agency_link_favorites anon policy
-- (cosmetic; launchpad writer not yet located), definer→invoker view
-- conversion deferred to a tested Phase B, tracker RPCs stay executable
-- by authenticated (they ARE app features; helpers are called from RLS
-- policies as authenticated — revoking would break both apps).

-- ============ 1) SECURITY DEFINER views: anon out, authenticated read-only
do $$
declare v text;
begin
  foreach v in array array[
    'ai_match_accuracy','personal_tasks_due','commission_ytd',
    'chargeback_risk_dashboard','medicare_plan_comparison',
    'agency_snapshot_trend','agency_snapshot_latest',
    'vw_uw_missing_doc_aging','vw_uw_stage_sla_breaches','vw_uw_bind_readiness',
    'vw_uw_carrier_turnaround','vw_ops_keyword_candidates',
    'vw_naics_candidate_expansion','vw_classification_resolved',
    'vw_classification_payload','portal_carrier_commissions'
  ] loop
    execute format('revoke all on public.%I from anon', v);
    execute format('revoke all on public.%I from authenticated', v);
    execute format('grant select on public.%I to authenticated', v);
  end loop;
end $$;

-- ============ 2) Explicit service_role policies on the 20 locked tables
do $$
declare t text;
begin
  foreach t in array array[
    'agency_crm_case_events','agency_crm_cases','agency_crm_document_links',
    'agency_crm_outbox','agency_crm_tasks','agency_crm_users','ams_writeback_log',
    'coi_drafts','commission_parity_report','crm_dispositions','crm_sync_log',
    'hermes_files','nowcerts_field_map','outbound_sync_queue_archive_2026_07_12',
    'renewal_checklist_items','renewal_documents','renewal_events',
    'renewal_outreach_drafts','renewal_templates','renewals_master'
  ] loop
    execute format('drop policy if exists "Service Role Full Access" on public.%I', t);
    execute format('create policy "Service Role Full Access" on public.%I for all to service_role using (true) with check (true)', t);
  end loop;
end $$;

-- ============ 3) Launchpad links: anon read-only, allowlist writes
drop policy if exists "anon all links" on public.agency_links;
drop policy if exists "anyone read links" on public.agency_links;
create policy "anyone read links" on public.agency_links
  for select to anon, authenticated using (true);
drop policy if exists "allowlist insert links" on public.agency_links;
create policy "allowlist insert links" on public.agency_links
  for insert to authenticated with check (is_commission_user());
drop policy if exists "allowlist update links" on public.agency_links;
create policy "allowlist update links" on public.agency_links
  for update to authenticated using (is_commission_user()) with check (is_commission_user());
drop policy if exists "allowlist delete links" on public.agency_links;
create policy "allowlist delete links" on public.agency_links
  for delete to authenticated using (is_commission_user());
-- agency_link_favorites intentionally left anon-writable (cosmetic star data).

-- ============ 4) Gate permissive authenticated policies on the allowlist
drop policy if exists "authenticated_all" on public.agency_tasks;
drop policy if exists "allowlist all agency_tasks" on public.agency_tasks;
create policy "allowlist all agency_tasks" on public.agency_tasks
  for all to authenticated using (is_commission_user()) with check (is_commission_user());

drop policy if exists "authenticated_update" on public.pipeline_issues;
drop policy if exists "allowlist update pipeline_issues" on public.pipeline_issues;
create policy "allowlist update pipeline_issues" on public.pipeline_issues
  for update to authenticated using (is_commission_user()) with check (is_commission_user());

-- ============ 5) Allowlist helpers: anon can no longer execute
-- NOTE: authenticated MUST keep EXECUTE — RLS policies on carrier/commission
-- tables call these as the querying (authenticated) role. Revoking from
-- authenticated breaks CarrierHub + Commission Tracker instantly.
revoke execute on function public.is_commission_user() from public, anon;
revoke execute on function public.is_commission_admin() from public, anon;
grant execute on function public.is_commission_user() to authenticated, service_role;
grant execute on function public.is_commission_admin() to authenticated, service_role;

-- ============ 6) Drop dead OpenClaw functions (queue table gone 2026-06-25)
do $$
declare r record;
begin
  for r in
    select p.oid::regprocedure as sig
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in ('openclaw_queue_stats','claim_openclaw_tasks',
                        'complete_openclaw_task','retry_openclaw_task')
  loop
    execute format('drop function %s', r.sig);
  end loop;
end $$;

-- ============ 7) Pin search_path on advisor-flagged functions
do $$
declare r record;
begin
  for r in
    select p.oid::regprocedure as sig, n.nspname as schema_name
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where (n.nspname = 'public' and p.proname in (
      'update_lamar_personal_updated_at','match_knowledge_chunks','match_gl_codes',
      'match_wc_codes','match_naics_codes','match_operations','get_commission_rule',
      'match_bundles','get_bundle_codes','classify_lead','set_updated_at',
      'run_classification_data_quality_checks','match_chunks','find_carriers',
      'update_personal_tasks_timestamp','get_due_reminders','get_morning_briefing_tasks',
      'complete_task','get_email_template','touch_updated_at','tg_carrier_appetite_touch',
      'add_wc_code','mark_canonical_inactive'))
      or (n.nspname = 'nowcerts_mirror' and p.proname = 'normalize_policy_number')
  loop
    if r.schema_name = 'public' then
      execute format('alter function %s set search_path = pg_catalog, public', r.sig);
    else
      execute format('alter function %s set search_path = pg_catalog, nowcerts_mirror, public', r.sig);
    end if;
  end loop;
end $$;
