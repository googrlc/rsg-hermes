-- =====================================================================================
-- OPPORTUNITIES — the new-business sales pipeline (Supabase-native)
-- Purpose: the pipeline "brain" that EspoCRM used to hold. NowCerts is the record
-- of truth for the insured/prospect (prospectType) and quotes (isQuote /
-- quoteStageName), but the agency's live book barely uses NowCerts quote stages
-- ("Received" only), so the real stage/next-action process lives here and is
-- mirrored down to NowCerts on write.
--
-- Grounded in a live read-only probe of NowCerts:
--   prospect_type  <- Insured.prospectType : Prospect | Hot_Prospect | Cold_Prospect
--   insured_type   <- Insured.insuredType  : Personal | Commercial
--   quote_number   <- Policy.number where Policy.isQuote = true
--   nowcerts_quote_guid <- Policy.databaseId (the quote row)
--   lead_source    <- Insured.leadSources / Policy.referralSourceName / origin
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.opportunities (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Stable staging key (normalized name+fein, etc.) so intake is idempotent
    -- before a NowCerts insured GUID exists.
    client_identifier     TEXT NOT NULL,

    -- NowCerts linkage (nullable until the insured/quote is created).
    insured_id            TEXT,                    -- nowcerts insured GUID
    insured_name          TEXT,
    nowcerts_quote_guid   TEXT,                    -- Policy.databaseId where isQuote=true
    quote_number          TEXT,                    -- Policy.number (quote)

    -- Prospect / segment (NowCerts vocab).
    prospect_type         TEXT,                    -- Prospect | Hot_Prospect | Cold_Prospect
    insured_type          TEXT,                    -- Personal | Commercial

    -- Pipeline.
    line_of_business      TEXT,
    stage                 TEXT NOT NULL DEFAULT 'New',   -- New | Info Gathering | Quoting | Quoted | Bound | Lost
    status                TEXT NOT NULL DEFAULT 'open',  -- open | won | lost
    premium_estimate      NUMERIC(12,2),
    carrier               TEXT,
    lead_source           TEXT,

    -- Workflow.
    next_action           TEXT,
    next_action_date      DATE,
    assigned_to           TEXT,
    lost_reason           TEXT,

    source                TEXT,                    -- intake source (cowork | manual | n8n | ...)
    created_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One open opportunity per client + line of business (idempotent intake).
    CONSTRAINT uq_opportunities_client_lob UNIQUE (client_identifier, line_of_business)
);

CREATE INDEX IF NOT EXISTS idx_opportunities_stage ON public.opportunities (stage);
CREATE INDEX IF NOT EXISTS idx_opportunities_status ON public.opportunities (status);
CREATE INDEX IF NOT EXISTS idx_opportunities_insured ON public.opportunities (insured_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_assigned ON public.opportunities (assigned_to);
CREATE INDEX IF NOT EXISTS idx_opportunities_quote_number ON public.opportunities (quote_number);

ALTER TABLE public.opportunities ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.opportunities;
CREATE POLICY "Service Role Full Access" ON public.opportunities
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated_select_opportunities" ON public.opportunities;
CREATE POLICY "authenticated_select_opportunities" ON public.opportunities
  FOR SELECT TO authenticated USING (true);
