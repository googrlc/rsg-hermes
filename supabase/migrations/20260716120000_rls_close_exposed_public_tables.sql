-- Close the four public tables Supabase's security advisor flagged with RLS
-- disabled (readable/writable by anon + authenticated over PostgREST):
--   property_lookups, dot_lookups, hazard_grades  — third-party reference data
--   outbound_sync_queue_archive_2026_07_12        — sync archive w/ client payloads
--
-- service_role bypasses RLS, so Hermes (which uses SUPABASE_SERVICE_ROLE_KEY)
-- is unaffected. The archive is internal-only: service_role write, no API read.
-- The reference tables allow authenticated SELECT for app/dashboard readers.

-- Idempotent replays
DROP POLICY IF EXISTS "Service Role Full Access" ON public.outbound_sync_queue_archive_2026_07_12;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.property_lookups;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.dot_lookups;
DROP POLICY IF EXISTS "Service Role Full Access" ON public.hazard_grades;
DROP POLICY IF EXISTS "authenticated_select_property_lookups" ON public.property_lookups;
DROP POLICY IF EXISTS "authenticated_select_dot_lookups"     ON public.dot_lookups;
DROP POLICY IF EXISTS "authenticated_select_hazard_grades"   ON public.hazard_grades;

ALTER TABLE public.outbound_sync_queue_archive_2026_07_12 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.property_lookups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dot_lookups      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hazard_grades    ENABLE ROW LEVEL SECURITY;

-- Backend (Hermes with service_role key): full access on all four.
CREATE POLICY "Service Role Full Access" ON public.outbound_sync_queue_archive_2026_07_12
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.property_lookups
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.dot_lookups
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.hazard_grades
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Reference tables: read-only for logged-in app/dashboard users.
-- (The archive intentionally gets NO authenticated policy — internal only.)
CREATE POLICY "authenticated_select_property_lookups" ON public.property_lookups
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_dot_lookups" ON public.dot_lookups
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_hazard_grades" ON public.hazard_grades
  FOR SELECT TO authenticated USING (true);
