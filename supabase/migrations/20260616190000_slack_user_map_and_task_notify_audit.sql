-- -------------------------------------------------------------------------------------
-- Slack ↔ EspoCRM user mapping and task notification audit tables
-- Used by: n8n workflows "EspoCRM Task Created → Slack Notify"
--          and "Slack Acknowledge → EspoCRM Write-back"
-- -------------------------------------------------------------------------------------

-- 1. slack_user_map — bidirectional lookup between EspoCRM user IDs and Slack user IDs
CREATE TABLE IF NOT EXISTS public.slack_user_map (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    espo_user_id VARCHAR(32) NOT NULL,
    slack_user_id VARCHAR(32) NOT NULL,
    display_name VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_slack_user_map_espo  UNIQUE (espo_user_id),
    CONSTRAINT uq_slack_user_map_slack UNIQUE (slack_user_id)
);

COMMENT ON TABLE public.slack_user_map IS 'Maps EspoCRM user IDs to Slack user IDs for notification routing.';

-- 2. task_notify_audit — append-only log of task notification events
CREATE TABLE IF NOT EXISTS public.task_notify_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id VARCHAR(32) NOT NULL,
    espo_user_id VARCHAR(32),
    slack_user_id VARCHAR(32),
    slack_channel VARCHAR(100),
    slack_ts VARCHAR(24),
    event VARCHAR(50) NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.task_notify_audit IS 'Append-only audit log for n8n task notification and acknowledgement events.';

CREATE INDEX IF NOT EXISTS idx_task_notify_audit_task_id ON public.task_notify_audit (task_id);
CREATE INDEX IF NOT EXISTS idx_task_notify_audit_event   ON public.task_notify_audit (event);

-- 3. Row Level Security
ALTER TABLE public.slack_user_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.task_notify_audit ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service Role Full Access" ON public.slack_user_map
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "Service Role Full Access" ON public.task_notify_audit
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "authenticated_select_slack_user_map" ON public.slack_user_map
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "authenticated_select_task_notify_audit" ON public.task_notify_audit
  FOR SELECT TO authenticated USING (true);

-- 4. Auto-update updated_at on slack_user_map
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = '';

DROP TRIGGER IF EXISTS trg_slack_user_map_updated_at ON public.slack_user_map;
CREATE TRIGGER trg_slack_user_map_updated_at
  BEFORE UPDATE ON public.slack_user_map
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
