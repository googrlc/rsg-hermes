-- Align public.outbound_sync_queue with Hermes (pipeline.py, crm_queue_worker).
-- Live drift: missing run_id / mapping_id, legacy column `attempts` instead of attempt_count.
-- Safe/idempotent: ADD IF NOT EXISTS, backfill, optional FKs.

-- -----------------------------------------------------------------------------
-- 1. Columns Hermes expects (see hermes/sync/pipeline.py _enqueue_outbound)
-- -----------------------------------------------------------------------------
ALTER TABLE public.outbound_sync_queue
  ADD COLUMN IF NOT EXISTS run_id UUID,
  ADD COLUMN IF NOT EXISTS mapping_id UUID,
  ADD COLUMN IF NOT EXISTS attempt_count INT;

-- Default attempt_count for existing rows
UPDATE public.outbound_sync_queue
SET attempt_count = COALESCE(attempt_count, 0)
WHERE attempt_count IS NULL;

ALTER TABLE public.outbound_sync_queue
  ALTER COLUMN attempt_count SET DEFAULT 0;

-- Not valid rows would violate NOT NULL — only enforce after backfill
ALTER TABLE public.outbound_sync_queue
  ALTER COLUMN attempt_count SET NOT NULL;

-- -----------------------------------------------------------------------------
-- 2. Migrate legacy `attempts` → attempt_count and drop `attempts`
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'outbound_sync_queue'
      AND column_name = 'attempts'
  ) THEN
    UPDATE public.outbound_sync_queue
    SET attempt_count = GREATEST(COALESCE(attempt_count, 0), COALESCE(attempts, 0));

    ALTER TABLE public.outbound_sync_queue DROP COLUMN attempts;
  END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 3. Optional FKs (only when parent tables exist; skip silently on edge DBs)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'sync_runs')
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'outbound_sync_queue_run_id_fkey') THEN
    ALTER TABLE public.outbound_sync_queue
      ADD CONSTRAINT outbound_sync_queue_run_id_fkey
      FOREIGN KEY (run_id) REFERENCES public.sync_runs(id) ON DELETE SET NULL;
  END IF;
EXCEPTION
  WHEN undefined_table THEN NULL;
  WHEN invalid_foreign_key THEN NULL;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'sync_mappings')
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'outbound_sync_queue_mapping_id_fkey') THEN
    ALTER TABLE public.outbound_sync_queue
      ADD CONSTRAINT outbound_sync_queue_mapping_id_fkey
      FOREIGN KEY (mapping_id) REFERENCES public.sync_mappings(id) ON DELETE SET NULL;
  END IF;
EXCEPTION
  WHEN undefined_table THEN NULL;
  WHEN invalid_foreign_key THEN NULL;
END $$;

ALTER TABLE public.outbound_sync_queue
  DROP CONSTRAINT IF EXISTS outbound_sync_queue_attempt_count_nonnegative;

ALTER TABLE public.outbound_sync_queue
  ADD CONSTRAINT outbound_sync_queue_attempt_count_nonnegative
  CHECK (attempt_count >= 0);

CREATE INDEX IF NOT EXISTS idx_outbound_queue_run_status
  ON public.outbound_sync_queue (run_id, status)
  WHERE run_id IS NOT NULL;

COMMENT ON COLUMN public.outbound_sync_queue.run_id IS 'sync_runs.id for this enqueue batch (Hermes pipeline)';
COMMENT ON COLUMN public.outbound_sync_queue.mapping_id IS 'sync_mappings.id when enqueue is tied to a crosswalk row';
COMMENT ON COLUMN public.outbound_sync_queue.attempt_count IS 'Retry counter; Hermes increments on each processing attempt';
