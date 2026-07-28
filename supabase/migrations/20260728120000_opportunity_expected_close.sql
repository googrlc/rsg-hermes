-- =====================================================================================
-- OPPORTUNITY — expected close date.
--
-- A pipeline without a close date is a list, not a forecast: there is no way to ask
-- "what lands this month" or to see a deal whose date has already slipped past.
--
-- NowCerts' Opportunity object has no estimated-close field. The nearest AMS dates are
-- neededBy (when the client needs coverage in force) and the quote's effective_date,
-- and both are really "when cover starts" rather than "when we expect to win it" — so
-- the forecast is CRM-owned and lives here. It is never written by the opportunity
-- sync; the AMS has nothing to overwrite it with.
--
-- Deliberately NOT backfilled from needed_by/effective_date. The API derives a
-- projected close (see hermes/intake/opportunities.projected_close) as
-- expected_close_date → needed_by → effective_date, and reports which one it used.
-- Copying the fallback into the column would freeze it: the row would stop tracking
-- the AMS date the moment it moved, while still looking like someone had set it.
-- =====================================================================================

ALTER TABLE public.opportunities
    ADD COLUMN IF NOT EXISTS expected_close_date DATE;

COMMENT ON COLUMN public.opportunities.expected_close_date IS
    'Projected close date — CRM-owned forecast, set by a human. Never written by the '
    'NowCerts opportunity sync. Blank means the projected close falls back to '
    'needed_by, then effective_date.';

CREATE INDEX IF NOT EXISTS idx_opportunities_expected_close
    ON public.opportunities (expected_close_date)
    WHERE expected_close_date IS NOT NULL;
