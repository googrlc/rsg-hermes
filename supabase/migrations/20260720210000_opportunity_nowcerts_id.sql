-- =====================================================================================
-- NowCerts Opportunity mirror key.
-- A NowCerts Opportunity is a first-class object (its own pipeline, distinct from
-- policies/quotes). The opportunity sync (hermes/sync/opportunity_sync.py) reads
-- OpportunitiesList and mirrors each row here, keyed by the NowCerts opportunity id.
-- Stages are stored VERBATIM from NowCerts (opportunityStageName) — the mirror must
-- reflect the AMS truth (e.g. 'Bound / Won', 'Annual Policy Review', 'Renewal in 30 days').
-- =====================================================================================

ALTER TABLE public.opportunities
    ADD COLUMN IF NOT EXISTS nowcerts_opportunity_id TEXT,
    ADD COLUMN IF NOT EXISTS needed_by DATE;   -- NowCerts neededBy (target/effective)

CREATE UNIQUE INDEX IF NOT EXISTS uq_opportunities_nowcerts_opportunity_id
    ON public.opportunities (nowcerts_opportunity_id)
    WHERE nowcerts_opportunity_id IS NOT NULL;

COMMENT ON COLUMN public.opportunities.nowcerts_opportunity_id IS 'NowCerts Opportunity id (OpportunitiesList.id) — the mirror/idempotency key for the opportunity sync.';
