-- Hermes: edge-case hardening — FK ergonomics, data integrity, updated_at UX, worker index.

-- 1. Reporting schedules survive Slack channel registry cleanup
ALTER TABLE public.reporting_schedules
  DROP CONSTRAINT IF EXISTS reporting_schedules_target_slack_channel_fkey;
ALTER TABLE public.reporting_schedules
  ADD CONSTRAINT reporting_schedules_target_slack_channel_fkey
    FOREIGN KEY (target_slack_channel)
    REFERENCES public.slack_registry(channel_id)
    ON DELETE SET NULL;

-- 2. Duplicate commission_audit rows same statement/policy/month → reject (idempotent upserts friendly)
CREATE UNIQUE INDEX IF NOT EXISTS commission_audits_stmt_policy_month_uniq
  ON public.commission_audits (statement_id, policy_number, snapshot_month);

-- 3. Guardrail severity whitelist (Hermes/tooling typo protection)
ALTER TABLE public.guardrail_logs DROP CONSTRAINT IF EXISTS guardrail_logs_severity_check;

UPDATE public.guardrail_logs
SET severity = 'MEDIUM'
WHERE severity IS NOT NULL AND severity NOT IN ('LOW', 'INFO', 'MEDIUM', 'HIGH', 'CRITICAL');

ALTER TABLE public.guardrail_logs
  ADD CONSTRAINT guardrail_logs_severity_check
    CHECK (
      severity IS NULL OR severity IN ('LOW', 'INFO', 'MEDIUM', 'HIGH', 'CRITICAL')
    );

-- 4. Queue integrity
ALTER TABLE public.crm_write_queue
  DROP CONSTRAINT IF EXISTS crm_write_queue_attempt_count_nonnegative;
ALTER TABLE public.crm_write_queue
  ADD CONSTRAINT crm_write_queue_attempt_count_nonnegative
    CHECK (attempt_count >= 0);

-- 5. Auto-maintain updated_at (Hermes tables only; immutable search_path on trigger wrapper)
CREATE OR REPLACE FUNCTION public.hermes_touch_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS hermes_touch_updated_at_crm_write_queue ON public.crm_write_queue;
CREATE TRIGGER hermes_touch_updated_at_crm_write_queue
  BEFORE UPDATE ON public.crm_write_queue
  FOR EACH ROW
  EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_commission_audits ON public.commission_audits;
CREATE TRIGGER hermes_touch_updated_at_commission_audits
  BEFORE UPDATE ON public.commission_audits
  FOR EACH ROW
  EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_project_85_renewals ON public.project_85_renewals;
CREATE TRIGGER hermes_touch_updated_at_project_85_renewals
  BEFORE UPDATE ON public.project_85_renewals
  FOR EACH ROW
  EXECUTE FUNCTION public.hermes_touch_updated_at();

-- 6. Worker-friendly partial index: rows still needing attention / retry
CREATE INDEX IF NOT EXISTS idx_crm_queue_open_work
  ON public.crm_write_queue (status, created_at)
  WHERE status IN ('PENDING', 'PROCESSING', 'FAILED', 'BLOCKED_BY_GUARDRAIL');
