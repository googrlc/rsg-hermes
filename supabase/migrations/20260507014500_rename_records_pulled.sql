-- Rename records_pulled → records_processed in sync_runs.
-- The original CREATE TABLE IF NOT EXISTS in 20260507010000 was updated to use
-- records_processed, but that change is a no-op on environments that already
-- applied the original migration. This ALTER covers those environments.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'sync_runs'
          AND column_name  = 'records_pulled'
    ) THEN
        ALTER TABLE public.sync_runs
            RENAME COLUMN records_pulled TO records_processed;
    END IF;
END
$$;
