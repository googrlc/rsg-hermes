-- =====================================================================================
-- RENEWAL CASES + TASKS — the renewal workspace container
-- Purpose: a renewal "case" is the working folder for ONE renewal event (worksheet,
-- research, tasks, filed documents, writeback state). Keyed by the same
-- renewal-event identity the eligibility engine uses (insured + policy lineage +
-- event date), so a case maps 1:1 to a renewal_candidates row.
--
-- renewal_tasks are the actionable to-dos under a case. Both are Supabase-native
-- (NowCerts is the system of record for policies; EspoCRM is out of this path).
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.renewal_cases (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Renewal-event identity (natural key — matches renewal_candidates).
    insured_id               TEXT NOT NULL,          -- nowcerts_insured_guid
    policy_lineage_id        TEXT NOT NULL,
    renewal_event_date       DATE NOT NULL,

    -- NowCerts identifiers / denormalized context for display.
    policy_number            TEXT,
    nowcerts_policy_guid     TEXT,
    client_name              TEXT,
    line_of_business         TEXT,
    segment                  TEXT,

    -- Workflow state.
    status                   TEXT NOT NULL DEFAULT 'open',   -- open | in_progress | blocked | complete
    assigned_to              TEXT,
    summary                  TEXT,

    -- Artifact links.
    worksheet_document_id    UUID,                   -- library/files row for the filed worksheet/PDF
    nextcloud_path           TEXT,                   -- where the case's documents are filed

    created_by               TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_renewal_cases_identity
      UNIQUE (insured_id, policy_lineage_id, renewal_event_date)
);

CREATE INDEX IF NOT EXISTS idx_renewal_cases_policy_number ON public.renewal_cases (policy_number);
CREATE INDEX IF NOT EXISTS idx_renewal_cases_status ON public.renewal_cases (status);
CREATE INDEX IF NOT EXISTS idx_renewal_cases_assigned ON public.renewal_cases (assigned_to);

CREATE TABLE IF NOT EXISTS public.renewal_tasks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id       UUID NOT NULL REFERENCES public.renewal_cases (id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    detail        TEXT,
    assigned_to   TEXT,
    due_date      DATE,
    status        TEXT NOT NULL DEFAULT 'open',       -- open | done
    source        TEXT,                               -- e.g. 'default_template' | 'manual'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One task title per case (idempotent default-template creation).
    CONSTRAINT uq_renewal_tasks_case_title UNIQUE (case_id, title)
);

CREATE INDEX IF NOT EXISTS idx_renewal_tasks_case ON public.renewal_tasks (case_id);
CREATE INDEX IF NOT EXISTS idx_renewal_tasks_status ON public.renewal_tasks (status);

ALTER TABLE public.renewal_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.renewal_tasks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.renewal_cases;
CREATE POLICY "Service Role Full Access" ON public.renewal_cases
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "authenticated_select_renewal_cases" ON public.renewal_cases;
CREATE POLICY "authenticated_select_renewal_cases" ON public.renewal_cases
  FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.renewal_tasks;
CREATE POLICY "Service Role Full Access" ON public.renewal_tasks
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "authenticated_select_renewal_tasks" ON public.renewal_tasks;
CREATE POLICY "authenticated_select_renewal_tasks" ON public.renewal_tasks
  FOR SELECT TO authenticated USING (true);
