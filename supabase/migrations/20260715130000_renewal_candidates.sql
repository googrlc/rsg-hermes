-- =====================================================================================
-- RENEWAL CANDIDATES — event-identity renewal model
-- Purpose: one row per upcoming *renewal event*, decided by the centralized
-- eligibility engine (hermes/renewals/eligibility.py). Keyed by renewal-event
-- identity (insured + policy lineage + event date), NOT policy_number alone.
--
-- project_85_renewals is rebuilt as a projection of ELIGIBLE candidates (its
-- rows share the candidate id), so existing consumers keep working while the
-- ~73%-stale policy_number-seeded rows fall out.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.renewal_candidates (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Renewal-event identity (the natural key).
    insured_id               TEXT NOT NULL,          -- nowcerts_insured_guid
    policy_lineage_id        TEXT NOT NULL,          -- derived predecessor/successor chain root
    renewal_event_date       DATE NOT NULL,          -- current term expiration = staged term effective

    -- NowCerts identifiers.
    nowcerts_policy_guid      TEXT,
    policy_number             TEXT,

    -- Live flags (NowCerts source of truth).
    insured_active            BOOLEAN,
    policy_active             BOOLEAN,

    -- Lifecycle.
    normalized_status         TEXT,                  -- Active / Up for Renewal / Renewing / Renewed / Expired / ...
    branch                    TEXT,                  -- current_term | staged_next_term | medicare_annual
    effective_date            DATE,
    expiration_date           DATE,

    -- Lineage.
    predecessor_policy_number TEXT,
    successor_policy_number   TEXT,

    -- Eligibility (the engine's verdict).
    eligibility_state         TEXT NOT NULL DEFAULT 'needs_verification',  -- eligible | needs_verification | excluded
    eligibility_reason        TEXT,
    last_verified_at          TIMESTAMPTZ,

    -- Segment / working-queue.
    segment                   TEXT,                  -- auto_6mo | personal_12mo | commercial_small | commercial_mid | benefits | medicare
    line_of_business          TEXT,
    client_name               TEXT,
    in_working_queue          BOOLEAN NOT NULL DEFAULT false,
    workflow_entry_date       DATE,

    -- Urgency (decoupled — describes an already-eligible event, never gates it).
    risk_status               TEXT,                  -- SAFE | AT_RISK | CRITICAL  (only for eligible rows)
    premium_current           NUMERIC(12,2),
    premium_renewal           NUMERIC(12,2),

    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_renewal_candidates_identity
      UNIQUE (insured_id, policy_lineage_id, renewal_event_date)
);

CREATE INDEX IF NOT EXISTS idx_renewal_candidates_state_queue
  ON public.renewal_candidates (eligibility_state, in_working_queue);
CREATE INDEX IF NOT EXISTS idx_renewal_candidates_insured ON public.renewal_candidates (insured_id);
CREATE INDEX IF NOT EXISTS idx_renewal_candidates_expiration ON public.renewal_candidates (expiration_date);
CREATE INDEX IF NOT EXISTS idx_renewal_candidates_policy_number ON public.renewal_candidates (policy_number);

ALTER TABLE public.renewal_candidates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.renewal_candidates;
CREATE POLICY "Service Role Full Access" ON public.renewal_candidates
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_renewal_candidates" ON public.renewal_candidates;
CREATE POLICY "authenticated_select_renewal_candidates" ON public.renewal_candidates
  FOR SELECT TO authenticated USING (true);

-- Note: renewal_candidates is an authoritative sidecar. project_85_renewals is
-- rebuilt as a projection of eligible candidates (keyed on policy_number, keeping
-- its existing renewal_actions / renewal_execution_receipts FKs). The executor
-- keeps its project_85_renewals(id) linkage and revalidates by re-running the
-- eligibility function on a fresh NowCerts read — no FK change needed here.
