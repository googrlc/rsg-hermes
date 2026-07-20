-- =====================================================================================
-- OPPORTUNITY ↔ NowCerts MIRROR
-- Make the Supabase opportunity mirror the NowCerts Opportunity form so a deal can be
-- worked from either side. Adds the NowCerts fields we were missing and remaps legacy
-- stage values to the NowCerts stage vocabulary.
--
--   opportunity_type  — NowCerts type list (New Business, Renewals, Cross-selling, …).
--                       'Renewals' is worked on the renewal pipeline; all others on the
--                       new-business pipeline (separate boards in the cockpit).
--   likelihood        — NowCerts-required win likelihood (Excellent..Not Likely). On our
--                       side it's a stage-driven % (probability) mapped to this category,
--                       defaulted to 'Good' so a NowCerts save never blocks. Editable in
--                       the CRM; NOT synced back to the AMS.
--   disposition       — NowCerts Disposition; free-text outcome (the AMS dropdown is
--                       currently empty) — kept off the stage list.
--   referral_source   — NowCerts Referral Source; READ-ONLY, pulled from the AMS by the
--                       sync (not editable in the CRM).
--   stage_due_date / closed_date — the NowCerts date fields.
-- =====================================================================================

ALTER TABLE public.opportunities
    ADD COLUMN IF NOT EXISTS opportunity_type TEXT NOT NULL DEFAULT 'New Business',
    ADD COLUMN IF NOT EXISTS likelihood       TEXT NOT NULL DEFAULT 'Good',
    ADD COLUMN IF NOT EXISTS disposition      TEXT,
    ADD COLUMN IF NOT EXISTS referral_source  TEXT,
    ADD COLUMN IF NOT EXISTS stage_due_date   DATE,
    ADD COLUMN IF NOT EXISTS closed_date      DATE;

-- Legacy stage values → NowCerts new-business stage vocabulary.
UPDATE public.opportunities SET stage = CASE stage
    WHEN 'New'            THEN 'Preparing Application'
    WHEN 'Info Gathering' THEN 'Preparing Application'
    WHEN 'Quoting'        THEN 'Sent For Quoting'
    WHEN 'Quoted'         THEN 'Quotes Received'
    ELSE stage END
WHERE stage IN ('New', 'Info Gathering', 'Quoting', 'Quoted');

-- One open opportunity per client + LOB is now per opportunity_type, so a renewal and a
-- new-business deal for the same client+LOB can coexist.
ALTER TABLE public.opportunities DROP CONSTRAINT IF EXISTS uq_opportunities_client_lob;
ALTER TABLE public.opportunities
    ADD CONSTRAINT uq_opportunities_client_lob_type UNIQUE (client_identifier, line_of_business, opportunity_type);

CREATE INDEX IF NOT EXISTS idx_opportunities_type ON public.opportunities (opportunity_type);

COMMENT ON COLUMN public.opportunities.likelihood IS 'NowCerts win likelihood (Excellent..Not Likely). Stage-driven on our side, defaulted to Good so a NowCerts save never blocks; editable in the CRM, not synced back to the AMS.';
COMMENT ON COLUMN public.opportunities.disposition IS 'NowCerts Disposition — free-text outcome (AMS dropdown currently empty); not a pipeline stage.';
COMMENT ON COLUMN public.opportunities.referral_source IS 'NowCerts Referral Source — READ-ONLY, pulled from the AMS by the sync; not editable in the CRM.';
