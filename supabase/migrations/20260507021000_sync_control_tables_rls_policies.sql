-- RLS + policies for sync tables (supplements create_sync_control_foundation / repo 20260507010000).
-- Applied remotely when foundation migration already created tables without RLS.

ALTER TABLE public.sync_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inbound_sync_staging ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbound_sync_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_errors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_conflicts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.sync_runs;
CREATE POLICY "Service Role Full Access" ON public.sync_runs
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "Service Role Full Access" ON public.inbound_sync_staging;
CREATE POLICY "Service Role Full Access" ON public.inbound_sync_staging
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "Service Role Full Access" ON public.sync_mappings;
CREATE POLICY "Service Role Full Access" ON public.sync_mappings
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "Service Role Full Access" ON public.outbound_sync_queue;
CREATE POLICY "Service Role Full Access" ON public.outbound_sync_queue
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "Service Role Full Access" ON public.sync_audit_log;
CREATE POLICY "Service Role Full Access" ON public.sync_audit_log
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "Service Role Full Access" ON public.sync_errors;
CREATE POLICY "Service Role Full Access" ON public.sync_errors
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "Service Role Full Access" ON public.sync_conflicts;
CREATE POLICY "Service Role Full Access" ON public.sync_conflicts
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_sync_runs" ON public.sync_runs;
CREATE POLICY "authenticated_select_sync_runs" ON public.sync_runs
  FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "authenticated_select_sync_mappings" ON public.sync_mappings;
CREATE POLICY "authenticated_select_sync_mappings" ON public.sync_mappings
  FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "authenticated_select_sync_audit_log" ON public.sync_audit_log;
CREATE POLICY "authenticated_select_sync_audit_log" ON public.sync_audit_log
  FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "authenticated_select_sync_conflicts" ON public.sync_conflicts;
CREATE POLICY "authenticated_select_sync_conflicts" ON public.sync_conflicts
  FOR SELECT TO authenticated USING (true);
