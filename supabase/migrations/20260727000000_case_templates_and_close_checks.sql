-- Case templates, required-task close checks, and the AMS summary split.
--
-- Three things this enables:
--
--   1. A case can be spawned from a template with its whole checklist attached,
--      so "new business onboarding" is one action instead of eight remembered
--      ones. template_key records which playbook produced it.
--
--   2. A case cannot be closed while a REQUIRED task is still open. Until now
--      "closed" meant only that somebody set status='closed' — the checklist was
--      decorative. is_required makes the checklist load-bearing.
--
--   3. The AMS gets a summary, not the checklist. resolution holds the
--      human-readable outcome that goes to NowCerts on close; the per-task
--      detail and timings stay here in the CRM, which is the system that
--      actually needs them for training and reporting.
--
-- Idempotent: every statement is IF NOT EXISTS / OR REPLACE. Existing rows keep
-- working — is_required defaults false, so no case already in flight suddenly
-- becomes unclosable.

-- ── Cases ────────────────────────────────────────────────────────────────────
ALTER TABLE public.agency_crm_cases
    ADD COLUMN IF NOT EXISTS template_key TEXT,
    ADD COLUMN IF NOT EXISTS resolution TEXT,
    ADD COLUMN IF NOT EXISTS resolved_by_email TEXT,
    ADD COLUMN IF NOT EXISTS ams_summary_sent_at TIMESTAMPTZ;

COMMENT ON COLUMN public.agency_crm_cases.template_key IS
    'Which case template produced this case (see hermes/casework/templates.py). '
    'NULL for ad-hoc cases created before templates or by hand.';
COMMENT ON COLUMN public.agency_crm_cases.resolution IS
    'Human-readable outcome, written on close. This is the text Hermes pushes to '
    'NowCerts as the case summary — the task checklist stays in the CRM.';
COMMENT ON COLUMN public.agency_crm_cases.ams_summary_sent_at IS
    'When the resolution summary was accepted by NowCerts. NULL = not yet pushed.';

-- ── Tasks ────────────────────────────────────────────────────────────────────
ALTER TABLE public.agency_crm_tasks
    ADD COLUMN IF NOT EXISTS is_required BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS sort_order INTEGER,
    ADD COLUMN IF NOT EXISTS template_key TEXT;

COMMENT ON COLUMN public.agency_crm_tasks.is_required IS
    'TRUE = this task blocks case closure. Enforced by the close endpoint and by '
    'the trigger below, so closing straight through PostgREST cannot bypass it.';
COMMENT ON COLUMN public.agency_crm_tasks.sort_order IS
    'Checklist display order within the case. NULL sorts last.';

-- Fast lookup of "what is still blocking this case".
CREATE INDEX IF NOT EXISTS idx_tasks_case_required_open
    ON public.agency_crm_tasks (case_id)
    WHERE is_required AND status <> 'completed' AND status <> 'cancelled';

-- ── Close guard ──────────────────────────────────────────────────────────────
-- Belt and braces alongside the API check: the CRM is reachable directly through
-- PostgREST, so the rule lives in the database too rather than only in one code
-- path. Cancelled tasks do not block — cancelling is the documented way to say
-- "this step didn't apply to this case".
CREATE OR REPLACE FUNCTION public.enforce_case_close_checks()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    blocking INTEGER;
BEGIN
    IF NEW.status = 'closed' AND COALESCE(OLD.status, '') <> 'closed' THEN
        SELECT count(*) INTO blocking
        FROM public.agency_crm_tasks t
        WHERE t.case_id = NEW.id
          AND t.is_required
          AND t.status NOT IN ('completed', 'cancelled');

        IF blocking > 0 THEN
            RAISE EXCEPTION
                'Cannot close case %: % required task(s) still open',
                NEW.case_number, blocking
                USING ERRCODE = 'check_violation';
        END IF;

        IF NEW.closed_at IS NULL THEN
            NEW.closed_at := now();
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_case_close_checks ON public.agency_crm_cases;
CREATE TRIGGER trg_enforce_case_close_checks
    BEFORE UPDATE ON public.agency_crm_cases
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_case_close_checks();

-- ── Task completion timestamp ────────────────────────────────────────────────
-- completed_at already exists but nothing guaranteed it was set; a task could
-- read 'completed' with no time, which makes cycle-time reporting impossible.
CREATE OR REPLACE FUNCTION public.stamp_task_completion()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'completed' AND COALESCE(OLD.status, '') <> 'completed' THEN
        IF NEW.completed_at IS NULL THEN
            NEW.completed_at := now();
        END IF;
    -- Reopening a task clears the stamp so it cannot claim a stale completion.
    ELSIF NEW.status <> 'completed' AND COALESCE(OLD.status, '') = 'completed' THEN
        NEW.completed_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_stamp_task_completion ON public.agency_crm_tasks;
CREATE TRIGGER trg_stamp_task_completion
    BEFORE UPDATE ON public.agency_crm_tasks
    FOR EACH ROW
    EXECUTE FUNCTION public.stamp_task_completion();

-- ── Progress view ────────────────────────────────────────────────────────────
-- What the cockpit needs to render a checklist and a "ready to close?" state
-- without N+1 queries.
CREATE OR REPLACE VIEW public.v_case_progress AS
SELECT
    c.id                AS case_id,
    c.case_number,
    c.case_type,
    c.template_key,
    c.title,
    c.insured_name,
    c.status,
    c.opened_at,
    c.closed_at,
    count(t.id)                                                        AS tasks_total,
    count(t.id) FILTER (WHERE t.status = 'completed')                  AS tasks_done,
    count(t.id) FILTER (WHERE t.is_required)                           AS required_total,
    count(t.id) FILTER (WHERE t.is_required AND t.status = 'completed') AS required_done,
    count(t.id) FILTER (
        WHERE t.is_required AND t.status NOT IN ('completed', 'cancelled')
    )                                                                  AS required_blocking,
    (count(t.id) FILTER (
        WHERE t.is_required AND t.status NOT IN ('completed', 'cancelled')
    ) = 0)                                                             AS can_close
FROM public.agency_crm_cases c
LEFT JOIN public.agency_crm_tasks t ON t.case_id = c.id
GROUP BY c.id;

COMMENT ON VIEW public.v_case_progress IS
    'Per-case checklist progress and whether every required task is satisfied. '
    'can_close is the same rule the close trigger enforces.';
