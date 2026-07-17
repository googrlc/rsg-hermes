-- =====================================================================================
-- PROPOSALS — standard client-facing proposals assembled from carrier quotes.
-- Purpose: bundle selected opportunity_quotes into one proposal for a client. Works
-- for commercial OR personal lines, single-LOB or multi-LOB (e.g. GL alone, or
-- GL + Workers Comp + Commercial Auto; personal auto alone, or Auto + Home +
-- Umbrella). The generator groups the included quotes by line of business, shows
-- carrier options per line, and rolls up a package total.
--
-- quote_ids holds the included opportunity_quotes.id values. The rendered document
-- (HTML always; PDF optional) is filed into the client's Nextcloud Proposals/ folder
-- (document_url/path) — same filer the quotes use.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.proposals (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Client linkage (a proposal is per-client; it can span several opportunities).
    client_identifier     TEXT,
    insured_id            TEXT,                    -- NowCerts insured GUID
    insured_name          TEXT,
    opportunity_id        UUID REFERENCES public.opportunities (id) ON DELETE SET NULL,

    -- The included carrier quotes (opportunity_quotes.id values).
    quote_ids             JSONB NOT NULL DEFAULT '[]'::jsonb,

    title                 TEXT,
    segment               TEXT,                    -- Personal | Commercial (label)
    proposal_type         TEXT NOT NULL DEFAULT 'New Business',  -- New Business | Renewal | Comparison
    notes                 TEXT,                    -- recommendation / cover narrative
    total_premium         NUMERIC(12,2),           -- rolled-up package total

    status                TEXT NOT NULL DEFAULT 'Draft',  -- Draft | Final | Sent

    -- Rendered document.
    content_html          TEXT,                    -- canonical generated HTML
    document_url          TEXT,                    -- Nextcloud WebDAV URL (filed copy)
    document_path         TEXT,
    document_filename     TEXT,
    pdf_url               TEXT,                    -- optional filed PDF
    pdf_path              TEXT,

    created_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_proposals_insured ON public.proposals (insured_id);
CREATE INDEX IF NOT EXISTS idx_proposals_opportunity ON public.proposals (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON public.proposals (status);
CREATE INDEX IF NOT EXISTS idx_proposals_client ON public.proposals (client_identifier);

ALTER TABLE public.proposals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.proposals;
CREATE POLICY "Service Role Full Access" ON public.proposals
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_proposals" ON public.proposals;
CREATE POLICY "authenticated_select_proposals" ON public.proposals
  FOR SELECT TO authenticated USING (true);
