-- =====================================================================================
-- OPPORTUNITY EVENTS — what happened on a deal.
--
-- Cases have agency_crm_case_events. Leads have crm_lead_notes. The pipeline — the one
-- surface the agency's revenue actually runs through — had neither: a single
-- `description` field that each edit overwrites, and stage moves applied in place with
-- no trace. So "who moved this to Lost, and when, and why" was unanswerable on exactly
-- the records where it matters most.
--
-- ONE table rather than separate notes and events, because the question people ask is
-- "what happened on this deal" and the answer interleaves the two: a call on Tuesday,
-- a stage move on Wednesday, the AMS filing on Friday. Splitting them would mean every
-- reader merges two lists by timestamp to get back to the thing they wanted.
--
--   event_type  note      — a human wrote it down
--               stage     — moved between stages (details carries from/to)
--               created   — the deal was opened, and by what (intake, lead conversion)
--               ams       — queued for, or blocked from, NowCerts
--   summary     one readable line. The timeline is skim-read; make it make sense alone.
--   details     the machine-readable version (from/to, queue id) for anything that
--               later wants to reconstruct rather than display.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.opportunity_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id  UUID NOT NULL REFERENCES public.opportunities(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL DEFAULT 'note',
    summary         TEXT NOT NULL,
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_email     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The timeline is always read newest-first for one deal.
CREATE INDEX IF NOT EXISTS idx_opportunity_events_deal
    ON public.opportunity_events (opportunity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_opportunity_events_type
    ON public.opportunity_events (event_type);

ALTER TABLE public.opportunity_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.opportunity_events;
CREATE POLICY "Service Role Full Access" ON public.opportunity_events
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_opportunity_events" ON public.opportunity_events;
CREATE POLICY "authenticated_select_opportunity_events" ON public.opportunity_events
    FOR SELECT TO authenticated USING (true);

COMMENT ON TABLE public.opportunity_events IS
    'A deal''s timeline — notes people write plus stage moves, creation and AMS filings, in one list. Cascades with the opportunity.';
