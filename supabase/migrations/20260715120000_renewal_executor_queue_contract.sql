-- =====================================================================================
-- RENEWAL EXECUTOR — JOB CONTRACT v2
-- Purpose: the controlled, queue-driven renewal executor. Hermes processes
-- human-approved renewal instructions staged in `outbound_sync_queue`
-- (object_type='renewal', destination_system='nowcerts') and writes an execution
-- receipt per job. Additive + idempotent (ADD IF NOT EXISTS, CREATE IF NOT EXISTS).
--
-- Supersedes Renewal Loop v6 (renewals_master / renewal_events / ams_writeback_log
-- stay in place as history; their writer, hermes/renewals/loop.py, is retired).
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- 1. APPROVAL TIMESTAMP on the outbound queue
--    (approved_by already exists from 20260507010000_sync_control_tables.sql)
-- -------------------------------------------------------------------------------------
ALTER TABLE public.outbound_sync_queue
  ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

COMMENT ON COLUMN public.outbound_sync_queue.approved_at IS
  'When a human approved this instruction. Renewal executor REQUIRES approved_by AND approved_at.';

-- Fast lookup for the executor claim query: only eligible renewal jobs.
CREATE INDEX IF NOT EXISTS idx_outbound_queue_renewal_ready
  ON public.outbound_sync_queue (created_at)
  WHERE object_type = 'renewal'
    AND destination_system = 'nowcerts'
    AND status = 'queued';

-- -------------------------------------------------------------------------------------
-- 2. EXECUTION RECEIPTS — one row per executor run (success, failure, or block).
--    This is the durable evidence store the contract's success criteria require:
--    before-state, requested change, resulting state, actor, NowCerts ids, verified.
--    A status of 'failed'/'blocked' row IS the actionable exception record.
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.renewal_execution_receipts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    queue_id          UUID REFERENCES public.outbound_sync_queue(id) ON DELETE SET NULL,
    renewal_id        UUID REFERENCES public.project_85_renewals(id) ON DELETE SET NULL,
    policy_number     TEXT,
    action            TEXT NOT NULL,               -- request_terms | prepare_options | client_follow_up | update_ams
    actor             TEXT,                        -- carried from outbound_sync_queue.approved_by
    approved_at       TIMESTAMPTZ,
    before_state      JSONB,                       -- NowCerts record read BEFORE the write
    requested_change  JSONB,                       -- the approved instruction (action + fields/expected_result)
    after_state       JSONB,                       -- NowCerts record read AFTER the write (verification read)
    verified          BOOLEAN NOT NULL DEFAULT false,
    nowcerts_ids      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {task_database_id, insured_database_id, policy_database_id, note_id}
    status            TEXT NOT NULL DEFAULT 'completed',   -- completed | failed | blocked
    error             TEXT,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_renewal_receipts_renewal_id ON public.renewal_execution_receipts(renewal_id);
CREATE INDEX IF NOT EXISTS idx_renewal_receipts_queue_id   ON public.renewal_execution_receipts(queue_id);
CREATE INDEX IF NOT EXISTS idx_renewal_receipts_status     ON public.renewal_execution_receipts(status, created_at);

-- -------------------------------------------------------------------------------------
-- 3. ROW LEVEL SECURITY — service_role full, authenticated read-only (mirrors
--    the project_85 / renewal_actions pattern in 20260501131246_*).
-- -------------------------------------------------------------------------------------
ALTER TABLE public.renewal_execution_receipts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.renewal_execution_receipts;
CREATE POLICY "Service Role Full Access" ON public.renewal_execution_receipts
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_renewal_execution_receipts" ON public.renewal_execution_receipts;
CREATE POLICY "authenticated_select_renewal_execution_receipts" ON public.renewal_execution_receipts
  FOR SELECT TO authenticated USING (true);
