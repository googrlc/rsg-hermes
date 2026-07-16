-- =====================================================================================
-- SCHEDULER_LOCKS — lease-based single-instance lock for the executor scheduler
-- Purpose: only ONE scheduler replica runs the intake/renewal executor cycle at a
-- time. A holder claims a named lock with a short TTL and renews it while working;
-- if it dies, the lease expires and another replica can take over. Implemented over
-- PostgREST (conditional update on expiry + insert-on-conflict), since the app has
-- no direct Postgres connection.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.scheduler_locks (
    lock_name    TEXT PRIMARY KEY,
    owner        TEXT NOT NULL,               -- host:pid:nonce of the holder
    acquired_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,        -- lease expiry; a free lock has expires_at < now()
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduler_locks_expires ON public.scheduler_locks (expires_at);

ALTER TABLE public.scheduler_locks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.scheduler_locks;
CREATE POLICY "Service Role Full Access" ON public.scheduler_locks
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_scheduler_locks" ON public.scheduler_locks;
CREATE POLICY "authenticated_select_scheduler_locks" ON public.scheduler_locks
  FOR SELECT TO authenticated USING (true);
