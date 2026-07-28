-- =====================================================================================
-- CRM LEADS — the lead station.
--
-- A lead is someone worth calling who is not yet a deal. Until now the Leads list was
-- a read-only view of NowCerts prospects: nothing could be added, nothing could be
-- worked, and there was nowhere to write down what was said on the phone.
--
-- Leads live HERE, not in the AMS. The agency's rule, same as the pipeline's: the CRM
-- is the working copy and NowCerts is the system of record for what is REAL. A name
-- and a phone number from a networking event is not a record of insurance, and filling
-- the AMS with prospects that never buy is how a book stops meaning anything. A lead
-- reaches NowCerts by being converted to an opportunity and that opportunity being
-- won — never before.
--
-- NowCerts prospects still show on the same list (read-only, source='nowcerts'), so
-- leads created directly in the AMS are not invisible here. `nowcerts_insured_guid`
-- links the two when a lead turns out to already exist there.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.crm_leads (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Who they are. `name` is the person or the business — whichever you would ask
    -- for at the door; `company` is set as well when the person represents one.
    name                    TEXT NOT NULL,
    company                 TEXT,
    email                   TEXT,
    phone                   TEXT,
    city                    TEXT,
    state                   TEXT,

    -- What the opportunity would be.
    lead_type               TEXT,            -- Personal | Commercial
    lines_of_business       TEXT,
    premium_estimate        NUMERIC(12,2),
    -- The date their current cover runs out: the whole reason to call in March
    -- rather than in September, and the column the list is ranked by.
    x_date                  DATE,

    -- Working it.
    status                  TEXT NOT NULL DEFAULT 'new',   -- new|working|quoted|converted|lost
    lead_source             TEXT,
    owner_email             TEXT REFERENCES public.agency_crm_users(email),
    next_action             TEXT,
    next_action_date        DATE,

    -- Links out. Both stay NULL for a lead that has not gone anywhere yet.
    nowcerts_insured_guid   TEXT,            -- set only if they also exist in the AMS
    converted_opportunity_id UUID REFERENCES public.opportunities(id) ON DELETE SET NULL,
    converted_at            TIMESTAMPTZ,
    lost_reason             TEXT,

    created_by_email        TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT crm_leads_status_check
        CHECK (status IN ('new', 'working', 'quoted', 'converted', 'lost'))
);

CREATE INDEX IF NOT EXISTS idx_crm_leads_status ON public.crm_leads (status);
CREATE INDEX IF NOT EXISTS idx_crm_leads_owner ON public.crm_leads (owner_email);
CREATE INDEX IF NOT EXISTS idx_crm_leads_x_date ON public.crm_leads (x_date)
    WHERE x_date IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_leads_nowcerts_guid
    ON public.crm_leads (nowcerts_insured_guid)
    WHERE nowcerts_insured_guid IS NOT NULL;

-- What was said on the phone. Append-only: a lead's history is the reason the next
-- call is not the same call again, so notes accumulate rather than overwrite.
CREATE TABLE IF NOT EXISTS public.crm_lead_notes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id       UUID NOT NULL REFERENCES public.crm_leads(id) ON DELETE CASCADE,
    body          TEXT NOT NULL,
    author_email  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_lead_notes_lead
    ON public.crm_lead_notes (lead_id, created_at DESC);

-- updated_at, so "nobody has touched this in three weeks" is answerable.
CREATE OR REPLACE FUNCTION public.crm_leads_touch()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_crm_leads_touch ON public.crm_leads;
CREATE TRIGGER trg_crm_leads_touch
    BEFORE UPDATE ON public.crm_leads
    FOR EACH ROW EXECUTE FUNCTION public.crm_leads_touch();

ALTER TABLE public.crm_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_lead_notes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.crm_leads;
CREATE POLICY "Service Role Full Access" ON public.crm_leads
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.crm_lead_notes;
CREATE POLICY "Service Role Full Access" ON public.crm_lead_notes
    FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.crm_leads IS
    'Lead station — CRM-owned prospects. Never written to NowCerts; a lead reaches the AMS only by being converted to an opportunity and that opportunity being won.';
COMMENT ON COLUMN public.crm_leads.x_date IS
    'When their current coverage expires — the date the list is ranked by.';
COMMENT ON COLUMN public.crm_leads.nowcerts_insured_guid IS
    'Set only when this lead also exists in the AMS (e.g. matched to a NowCerts prospect). Does NOT mean we pushed them there.';
