-- =====================================================================================
-- RENEWAL CASE DETAILS — renewal-event identity for the shared agency CRM cases
-- Purpose (#113): Hermes renewal cases now live in the shared public.agency_crm_cases
-- table (case_type='renewal') alongside marketing/service/claims/etc., instead of a
-- separate renewal_cases/renewal_tasks system. This 1:1 detail table carries the
-- renewal-only attributes + the renewal-event identity (insured + policy lineage +
-- event date + NowCerts policy GUID) and enforces ONE renewal case per event, without
-- adding renewal-specific columns to the shared cases table.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.renewal_case_details (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id               UUID NOT NULL REFERENCES public.agency_crm_cases (id) ON DELETE CASCADE,

    -- Renewal-event identity (the natural key — matches renewal_candidates).
    insured_id            TEXT NOT NULL,          -- nowcerts insured GUID
    policy_lineage_id     TEXT NOT NULL,
    renewal_event_date    DATE NOT NULL,
    nowcerts_policy_guid  TEXT,

    -- Renewal-only attributes not present on the shared cases table.
    line_of_business      TEXT,
    segment               TEXT,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_renewal_case_details_identity
        UNIQUE (insured_id, policy_lineage_id, renewal_event_date),
    CONSTRAINT uq_renewal_case_details_case UNIQUE (case_id)  -- strictly 1:1 with a case
);

CREATE INDEX IF NOT EXISTS idx_renewal_case_details_case ON public.renewal_case_details (case_id);
CREATE INDEX IF NOT EXISTS idx_renewal_case_details_insured ON public.renewal_case_details (insured_id);

ALTER TABLE public.renewal_case_details ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.renewal_case_details;
CREATE POLICY "Service Role Full Access" ON public.renewal_case_details
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_renewal_case_details" ON public.renewal_case_details;
CREATE POLICY "authenticated_select_renewal_case_details" ON public.renewal_case_details
  FOR SELECT TO authenticated USING (true);
