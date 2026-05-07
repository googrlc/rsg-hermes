-- Align remote schema (from create_sync_control_foundation) with Hermes Python sync code expectations.
-- Safe to run after foundation migration; uses IF NOT EXISTS / idempotent patterns.

-- sync_runs: pipeline + bidirectional insert direction / error_summary
ALTER TABLE public.sync_runs ADD COLUMN IF NOT EXISTS direction TEXT DEFAULT 'nowcerts_to_espocrm';
ALTER TABLE public.sync_runs ADD COLUMN IF NOT EXISTS error_summary TEXT;

-- inbound_sync_staging: pipeline upsert keyed by run_id + source triple
ALTER TABLE public.inbound_sync_staging ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES public.sync_runs(id) ON DELETE CASCADE;
ALTER TABLE public.inbound_sync_staging ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS idx_staging_run_source
    ON public.inbound_sync_staging (run_id, source_system, source_object_type, source_object_id);

-- sync_audit_log: bidirectional._audit expects source_object_id + optional message
ALTER TABLE public.sync_audit_log ADD COLUMN IF NOT EXISTS source_object_id TEXT;
ALTER TABLE public.sync_audit_log ADD COLUMN IF NOT EXISTS dest_object_id TEXT;
ALTER TABLE public.sync_audit_log ADD COLUMN IF NOT EXISTS message TEXT;

-- updated_at trigger on inbound staging when column exists
DROP TRIGGER IF EXISTS hermes_touch_updated_at_inbound_sync_staging ON public.inbound_sync_staging;
CREATE TRIGGER hermes_touch_updated_at_inbound_sync_staging
  BEFORE UPDATE ON public.inbound_sync_staging
  FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();
