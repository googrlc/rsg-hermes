-- Async OpenClaw AI enrichment task queue.
CREATE TABLE IF NOT EXISTS public.openclaw_task_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    status sync_status NOT NULL DEFAULT 'PENDING',
    priority INT NOT NULL DEFAULT 5,
    attempt_count INT NOT NULL DEFAULT 0,
    requested_by VARCHAR(100) NOT NULL DEFAULT 'dashboard',
    notify_slack BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.openclaw_task_queue
  DROP CONSTRAINT IF EXISTS openclaw_task_queue_attempt_count_nonnegative;
ALTER TABLE public.openclaw_task_queue
  ADD CONSTRAINT openclaw_task_queue_attempt_count_nonnegative
  CHECK (attempt_count >= 0);

ALTER TABLE public.openclaw_task_queue
  DROP CONSTRAINT IF EXISTS openclaw_task_queue_priority_positive;
ALTER TABLE public.openclaw_task_queue
  ADD CONSTRAINT openclaw_task_queue_priority_positive
  CHECK (priority >= 1);

CREATE INDEX IF NOT EXISTS idx_openclaw_task_queue_status_created
  ON public.openclaw_task_queue (status, priority, created_at);

ALTER TABLE public.openclaw_task_queue ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.openclaw_task_queue;
CREATE POLICY "Service Role Full Access" ON public.openclaw_task_queue
  FOR ALL TO service_role USING (true) WITH CHECK (true);
