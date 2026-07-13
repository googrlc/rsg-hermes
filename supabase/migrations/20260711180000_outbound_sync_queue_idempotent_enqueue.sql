-- Idempotent outbound enqueue + terminal 'dead' state for outbound_sync_queue.
--
-- Problem this fixes: the daily sync re-enqueued the SAME object as a brand-new
-- row every run (attempt_count=1 everywhere), so 257 real objects grew into
-- 1,130+ 'failed' rows. Enqueue was not idempotent.
--
-- Fix: a partial UNIQUE index over the open ('queued') work items. Combined with
-- the pre-insert existence check + swallow-on-conflict in
-- hermes/sync/pipeline.py::_enqueue_outbound, a given (object_type, object_id,
-- destination_system, action) can have at most ONE open queued row at a time.
--
-- NOTE on NULL object_id: create-actions carry object_id = NULL (the Espo id
-- doesn't exist yet). NULLs are distinct in a UNIQUE index, so create rows
-- BYPASS this dedupe by design — the create path is de-duplicated upstream by
-- mapping resolution + normalized-name matching instead. Acceptable and
-- intentional.
--
-- Safe/idempotent: IF NOT EXISTS throughout; no data rewrite.

-- -----------------------------------------------------------------------------
-- 1. Race-safe idempotency backstop for open (queued) enqueues.
-- -----------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_outbound_queue_open_work
  ON public.outbound_sync_queue (object_type, object_id, destination_system, action)
  WHERE status = 'queued';

-- -----------------------------------------------------------------------------
-- 2. Drain support: find due 'queued' rows quickly (scheduler polls this).
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_outbound_queue_due
  ON public.outbound_sync_queue (status, scheduled_for)
  WHERE status = 'queued';

-- -----------------------------------------------------------------------------
-- 3. Document the lifecycle. Vocabulary is unchanged except for ONE added
--    terminal state, 'dead', for rows that can never succeed (e.g. the Espo
--    target was purged). No renames on the live table. `status` stays free
--    text (no enum/CHECK) so this is additive and reversible.
-- -----------------------------------------------------------------------------
COMMENT ON COLUMN public.outbound_sync_queue.status IS
  'Lifecycle: queued -> processing -> completed | failed (retriable) | dead (terminal, e.g. target_purged). '
  'At most one queued row per (object_type, object_id, destination_system, action) via uq_outbound_queue_open_work.';
