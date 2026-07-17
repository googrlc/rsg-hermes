-- =====================================================================================
-- OPPORTUNITY_QUOTES — carrier quotes attached to a pipeline opportunity.
-- Purpose: a quote is a first-class record (one opportunity can hold several
-- carrier quotes). Each quote carries the carrier's terms, the actual quote PDF
-- (filed into the client's Nextcloud Quotes/ folder — document_url/path point at
-- it), and its NowCerts linkage once the approval-gated write-back runs.
--
-- The write-back path is unchanged in spirit: a quote is a NowCerts Policy with
-- IsQuote=true, enqueued to outbound_sync_queue (object_type='quote') and
-- written by hermes/quotes/executor.py after human approval.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.opportunity_quotes (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Parent opportunity (cascade so quotes die with the opportunity).
    opportunity_id        UUID NOT NULL REFERENCES public.opportunities (id) ON DELETE CASCADE,

    -- Denormalized client linkage (mirrors the opportunity so the Quotes module
    -- can list/sort without a join, and the AMS write has the insured GUID).
    client_identifier     TEXT,
    insured_id            TEXT,                    -- NowCerts insured GUID
    insured_name          TEXT,

    -- Quote terms.
    line_of_business      TEXT,
    carrier               TEXT,
    premium               NUMERIC(12,2),
    effective_date        DATE,
    expiration_date       DATE,
    notes                 TEXT,

    -- NowCerts linkage (nullable until the quote executor writes it).
    quote_number          TEXT,                    -- Policy.number where isQuote=true
    nowcerts_quote_guid   TEXT,                    -- Policy.databaseId (the quote row)

    -- The actual quote PDF in Nextcloud (Clients/{client}/Quotes/).
    document_url          TEXT,                    -- WebDAV URL to the file
    document_path         TEXT,                    -- Nextcloud relative path
    document_filename     TEXT,

    -- Draft | Queued | Sent | Bound | Lost. Queued/Sent track the AMS write-back.
    status                TEXT NOT NULL DEFAULT 'Draft',

    created_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_opportunity_quotes_opportunity ON public.opportunity_quotes (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_opportunity_quotes_insured ON public.opportunity_quotes (insured_id);
CREATE INDEX IF NOT EXISTS idx_opportunity_quotes_status ON public.opportunity_quotes (status);
CREATE INDEX IF NOT EXISTS idx_opportunity_quotes_quote_number ON public.opportunity_quotes (quote_number);

ALTER TABLE public.opportunity_quotes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.opportunity_quotes;
CREATE POLICY "Service Role Full Access" ON public.opportunity_quotes
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_opportunity_quotes" ON public.opportunity_quotes;
CREATE POLICY "authenticated_select_opportunity_quotes" ON public.opportunity_quotes
  FOR SELECT TO authenticated USING (true);
