-- Hermes AI: bind RLS to Postgres role service_role so backend read/write works reliably.
-- Drops JWT-role checks in favor of FOR ALL TO service_role (Supabase canonical pattern).

-- Remove prior JWT-based Hermes policies
DROP POLICY IF EXISTS "service_role_full_access_slack_registry" ON public.slack_registry;
DROP POLICY IF EXISTS "service_role_full_access_hermes_ai_roles" ON public.hermes_ai_roles;
DROP POLICY IF EXISTS "service_role_full_access_crm_write_queue" ON public.crm_write_queue;
DROP POLICY IF EXISTS "service_role_full_access_crm_receipts" ON public.crm_receipts;
DROP POLICY IF EXISTS "service_role_full_access_commission_audits" ON public.commission_audits;
DROP POLICY IF EXISTS "service_role_full_access_eom_scorecards" ON public.eom_scorecards;
DROP POLICY IF EXISTS "service_role_full_access_project_85_renewals" ON public.project_85_renewals;
DROP POLICY IF EXISTS "service_role_full_access_renewal_actions" ON public.renewal_actions;
DROP POLICY IF EXISTS "service_role_full_access_guardrail_logs" ON public.guardrail_logs;
DROP POLICY IF EXISTS "service_role_full_access_reporting_schedules" ON public.reporting_schedules;
DROP POLICY IF EXISTS "service_role_full_access_dashboard_kpis" ON public.dashboard_kpis;

DROP POLICY IF EXISTS "authenticated_select_slack_registry" ON public.slack_registry;
DROP POLICY IF EXISTS "authenticated_select_hermes_ai_roles" ON public.hermes_ai_roles;
DROP POLICY IF EXISTS "authenticated_select_commission_audits" ON public.commission_audits;
DROP POLICY IF EXISTS "authenticated_select_eom_scorecards" ON public.eom_scorecards;
DROP POLICY IF EXISTS "authenticated_select_project_85_renewals" ON public.project_85_renewals;
DROP POLICY IF EXISTS "authenticated_select_renewal_actions" ON public.renewal_actions;
DROP POLICY IF EXISTS "authenticated_select_guardrail_logs" ON public.guardrail_logs;
DROP POLICY IF EXISTS "authenticated_select_reporting_schedules" ON public.reporting_schedules;
DROP POLICY IF EXISTS "authenticated_select_dashboard_kpis" ON public.dashboard_kpis;

-- Idempotent replays / alignment with patched baseline migration file
DROP POLICY IF EXISTS "Service Role Full Access" ON public.slack_registry;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.hermes_ai_roles;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.crm_write_queue;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.crm_receipts;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.commission_audits;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.eom_scorecards;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.project_85_renewals;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.renewal_actions;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.guardrail_logs;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.reporting_schedules;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.dashboard_kpis;

DROP POLICY IF EXISTS "Users can view commissions" ON public.commission_audits;
DROP POLICY IF EXISTS "Users can view renewals" ON public.project_85_renewals;

-- Backend (Hermes with service_role key): full access on every Hermes table
CREATE POLICY "Service Role Full Access" ON public.slack_registry
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.hermes_ai_roles
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.crm_write_queue
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.crm_receipts
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.commission_audits
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.eom_scorecards
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.project_85_renewals
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.renewal_actions
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.guardrail_logs
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.reporting_schedules
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.dashboard_kpis
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Authenticated dashboard readers (narrow CRM visibility: receipts/queue Hermes-backend only)
CREATE POLICY "authenticated_select_slack_registry" ON public.slack_registry
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_hermes_ai_roles" ON public.hermes_ai_roles
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "Users can view commissions" ON public.commission_audits
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_eom_scorecards" ON public.eom_scorecards
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "Users can view renewals" ON public.project_85_renewals
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_renewal_actions" ON public.renewal_actions
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "authenticated_select_guardrail_logs" ON public.guardrail_logs
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_reporting_schedules" ON public.reporting_schedules
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_dashboard_kpis" ON public.dashboard_kpis
  FOR SELECT TO authenticated USING (true);
