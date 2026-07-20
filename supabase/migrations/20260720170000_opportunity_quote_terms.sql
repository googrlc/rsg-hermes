-- =====================================================================================
-- OPPORTUNITY QUOTE TERMS — the real terms pulled back from a NowCerts quote.
-- A NowCerts quote is a Policy with IsQuote=true (see opportunities_pipeline.sql).
-- The quotes → pipeline sync (hermes/sync/nowcerts_pipeline_sync.py) reads those
-- quotes and lands their live terms on the opportunity so the pipeline shows the
-- actual number being worked, not just the intake estimate.
--
-- These are QUOTE terms, not a bound policy — an opportunity is still a working
-- deal. carrier / quote_number / nowcerts_quote_guid already exist; these are the
-- fields that had no home.
-- =====================================================================================

ALTER TABLE public.opportunities
    ADD COLUMN IF NOT EXISTS premium_actual    NUMERIC(12,2),  -- real quoted premium (vs premium_estimate)
    ADD COLUMN IF NOT EXISTS effective_date    DATE,           -- quote effective date
    ADD COLUMN IF NOT EXISTS expiration_date   DATE,           -- quote expiration (x-date)
    ADD COLUMN IF NOT EXISTS policy_status     TEXT,           -- NowCerts quote/policy status string
    ADD COLUMN IF NOT EXISTS synced_at         TIMESTAMPTZ,    -- last successful NowCerts pull
    ADD COLUMN IF NOT EXISTS sync_source       TEXT;           -- quotes-sync | quote-push | ...

COMMENT ON COLUMN public.opportunities.premium_actual IS 'Real premium pulled from the NowCerts quote; premium_estimate is the pre-AMS guess.';
COMMENT ON COLUMN public.opportunities.expiration_date IS 'Quote expiration (x-date) — powers renewal/cross-sell timing on the pipeline.';
COMMENT ON COLUMN public.opportunities.sync_source IS 'How the quote terms were pulled: quotes-sync (inbound NowCerts sweep) or quote-push (immediate on write-back).';

CREATE INDEX IF NOT EXISTS idx_opportunities_expiration ON public.opportunities (expiration_date);
