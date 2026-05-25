-- =====================================================================================
-- AGENCY MEMORY RETRIEVAL TABLES
-- Purpose: Backing store for the Hermes skill family that turns intake summaries into
-- both CRM records (system of record) and indexed knowledge (memory layer).
--
-- Consumed by .claude/skills/{crm-fact-retriever, crm-intake-writer, crm-upsert-planner,
-- commercial-risk-intake, personal-lines-intake, life-insurance-intake, benefits-intake,
-- renewal-review, carrier-appetite, proposal-builder}.
--
-- See docs/agency-memory-plan.md §6 for the architecture.
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- Sensitivity enum (used across all retrieval tables)
-- -------------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'retrieval_sensitivity') THEN
    CREATE TYPE retrieval_sensitivity AS ENUM ('standard', 'restricted');
  END IF;
END$$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'retrieval_confidence') THEN
    CREATE TYPE retrieval_confidence AS ENUM ('low', 'medium', 'high');
  END IF;
END$$;

-- -------------------------------------------------------------------------------------
-- client_entities — canonical retrieval index for any entity (Account/Contact/Opp)
-- Lets facts/notes/docs point at a stable retrieval id even when CRM ids are missing.
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.client_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type VARCHAR(50) NOT NULL,
        -- Allowed values: Account, Contact, Household, Opportunity, Policy, Renewal, Lead
    entity_name VARCHAR(500) NOT NULL,
    crm_account_id VARCHAR(100),
    crm_contact_id VARCHAR(100),
    crm_opportunity_id VARCHAR(100),
    crm_policy_id VARCHAR(100),
    crm_renewal_id VARCHAR(100),
    crm_lead_id VARCHAR(100),
    canonical_aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- e.g. ["3D Pumps LLC", "3D Pumps", "3DP LLC"]
    primary_account_entity_id UUID REFERENCES public.client_entities(id) ON DELETE SET NULL,
        -- For Contact rows, points at the canonical Account/Household entity.
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT client_entities_entity_type_check CHECK (
        entity_type IN ('Account', 'Contact', 'Household', 'Opportunity', 'Policy', 'Renewal', 'Lead')
    )
);

CREATE INDEX IF NOT EXISTS idx_client_entities_entity_name
    ON public.client_entities (entity_name);
CREATE INDEX IF NOT EXISTS idx_client_entities_crm_account
    ON public.client_entities (crm_account_id) WHERE crm_account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_client_entities_crm_contact
    ON public.client_entities (crm_contact_id) WHERE crm_contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_client_entities_crm_opportunity
    ON public.client_entities (crm_opportunity_id) WHERE crm_opportunity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_client_entities_aliases
    ON public.client_entities USING GIN (canonical_aliases);
CREATE INDEX IF NOT EXISTS idx_client_entities_tags
    ON public.client_entities USING GIN (tags);

-- -------------------------------------------------------------------------------------
-- client_relationships — person-to-entity links (Principal, Spouse, Beneficiary, etc.)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.client_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_entity_id UUID NOT NULL REFERENCES public.client_entities(id) ON DELETE CASCADE,
    to_entity_id UUID NOT NULL REFERENCES public.client_entities(id) ON DELETE CASCADE,
    relationship_type VARCHAR(50) NOT NULL,
        -- e.g. Principal, Spouse, Child, Decision Maker, Beneficiary, Owner, Referral Partner
    role_detail VARCHAR(200),
    sensitivity retrieval_sensitivity NOT NULL DEFAULT 'standard',
    source VARCHAR(200),
    confidence retrieval_confidence NOT NULL DEFAULT 'high',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT client_relationships_no_self_link CHECK (from_entity_id <> to_entity_id),
    CONSTRAINT client_relationships_unique UNIQUE (from_entity_id, to_entity_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_client_relationships_from
    ON public.client_relationships (from_entity_id);
CREATE INDEX IF NOT EXISTS idx_client_relationships_to
    ON public.client_relationships (to_entity_id);

-- -------------------------------------------------------------------------------------
-- client_facts — structured key/value facts (EIN, phone, DOB, payroll, etc.)
-- Primary table for crm-fact-retriever lookups.
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.client_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.client_entities(id) ON DELETE CASCADE,
    fact_label VARCHAR(100) NOT NULL,
        -- e.g. EIN, Phone, Email, Address, Date of Birth, Annual Revenue, Estimated Payroll
    fact_value TEXT NOT NULL,
    fact_value_normalized TEXT,
        -- Normalized form for matching (lowercased email, digits-only phone, etc.)
    sensitivity retrieval_sensitivity NOT NULL DEFAULT 'standard',
    confidence retrieval_confidence NOT NULL DEFAULT 'high',
    source VARCHAR(500) NOT NULL,
        -- e.g. "underwriting summary", "EspoCRM Contact.phoneNumber", "PDF page 3"
    source_date DATE,
    source_ref VARCHAR(500),
        -- URL, file name, message_ts, or quote number
    superseded_at TIMESTAMPTZ,
        -- Set when a newer fact replaces this one. NULL = currently active.
    superseded_by UUID REFERENCES public.client_facts(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_client_facts_entity_label
    ON public.client_facts (entity_id, fact_label);
CREATE INDEX IF NOT EXISTS idx_client_facts_label_active
    ON public.client_facts (fact_label) WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_client_facts_value_normalized
    ON public.client_facts (fact_value_normalized)
    WHERE fact_value_normalized IS NOT NULL AND superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_client_facts_sensitivity_active
    ON public.client_facts (sensitivity) WHERE superseded_at IS NULL;

-- -------------------------------------------------------------------------------------
-- client_notes — structured narrative notes (paired with EspoCRM ClientNote)
-- Body produced by crm-note-structurer; summary indexed here for retrieval.
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.client_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.client_entities(id) ON DELETE CASCADE,
    crm_account_id VARCHAR(100),
    crm_contact_id VARCHAR(100),
    crm_opportunity_id VARCHAR(100),
    crm_client_note_id VARCHAR(100),
        -- The Espo ClientNote id once written. NULL until the queue worker reports back.
    note_type VARCHAR(50) NOT NULL,
        -- Underwriting Summary, Quote Summary, Discovery Call, Renewal Review,
        -- Service Request, Claim Note, Carrier Appetite Note, Internal Strategy Note,
        -- Email Recap, Meeting Summary, Voicemail / No Contact
    title VARCHAR(500) NOT NULL,
    summary TEXT NOT NULL,
        -- One-paragraph headline used by retrieval; full body in full_text.
    full_text TEXT,
    audience VARCHAR(20) NOT NULL DEFAULT 'internal',
        -- internal | client_safe
    sensitivity retrieval_sensitivity NOT NULL DEFAULT 'standard',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    author VARCHAR(200),
    note_date DATE,
    source VARCHAR(500),
    source_ref VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT client_notes_audience_check CHECK (audience IN ('internal', 'client_safe')),
    CONSTRAINT client_notes_note_type_check CHECK (note_type IN (
        'Underwriting Summary',
        'Quote Summary',
        'Discovery Call',
        'Renewal Review',
        'Service Request',
        'Claim Note',
        'Carrier Appetite Note',
        'Internal Strategy Note',
        'Email Recap',
        'Meeting Summary',
        'Voicemail / No Contact'
    ))
);

CREATE INDEX IF NOT EXISTS idx_client_notes_entity
    ON public.client_notes (entity_id);
CREATE INDEX IF NOT EXISTS idx_client_notes_note_type
    ON public.client_notes (note_type);
CREATE INDEX IF NOT EXISTS idx_client_notes_crm_account
    ON public.client_notes (crm_account_id) WHERE crm_account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_client_notes_crm_opportunity
    ON public.client_notes (crm_opportunity_id) WHERE crm_opportunity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_client_notes_tags
    ON public.client_notes USING GIN (tags);

-- -------------------------------------------------------------------------------------
-- client_documents — references to source documents (PDFs, emails, transcripts)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.client_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.client_entities(id) ON DELETE CASCADE,
    crm_account_id VARCHAR(100),
    file_name VARCHAR(500) NOT NULL,
    document_type VARCHAR(50) NOT NULL,
        -- application, acord, loss_runs, quote_proposal, renewal_letter, transcript,
        -- email, fact_finder, policy, certificate, screenshot, other
    storage_url VARCHAR(1000),
    extracted_text_url VARCHAR(1000),
    summary TEXT,
    page_count INT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    sensitivity retrieval_sensitivity NOT NULL DEFAULT 'standard',
    received_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_client_documents_entity
    ON public.client_documents (entity_id);
CREATE INDEX IF NOT EXISTS idx_client_documents_doc_type
    ON public.client_documents (document_type);
CREATE INDEX IF NOT EXISTS idx_client_documents_tags
    ON public.client_documents USING GIN (tags);

-- -------------------------------------------------------------------------------------
-- quote_facts — per-quote financial detail (one row per quote line)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.quote_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.client_entities(id) ON DELETE CASCADE,
    crm_opportunity_id VARCHAR(100),
    quote_number VARCHAR(100) NOT NULL,
    line_of_business VARCHAR(100) NOT NULL,
    carrier VARCHAR(200),
    premium DECIMAL(14,2),
    fees DECIMAL(14,2),
    taxes DECIMAL(14,2),
    total DECIMAL(14,2),
    effective_date DATE,
    expiration_date DATE,
    status VARCHAR(50),
        -- Indication, Firm, Bound, Declined, Withdrawn, Expired
    coverage_limits JSONB,
    deductibles JSONB,
    endorsements JSONB,
    source VARCHAR(500),
    source_ref VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT quote_facts_unique UNIQUE (quote_number, line_of_business)
);

CREATE INDEX IF NOT EXISTS idx_quote_facts_entity
    ON public.quote_facts (entity_id);
CREATE INDEX IF NOT EXISTS idx_quote_facts_quote_number
    ON public.quote_facts (quote_number);
CREATE INDEX IF NOT EXISTS idx_quote_facts_lob
    ON public.quote_facts (line_of_business);
CREATE INDEX IF NOT EXISTS idx_quote_facts_crm_opportunity
    ON public.quote_facts (crm_opportunity_id) WHERE crm_opportunity_id IS NOT NULL;

-- -------------------------------------------------------------------------------------
-- policy_facts — per-policy detail (bound coverage)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.policy_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.client_entities(id) ON DELETE CASCADE,
    crm_policy_id VARCHAR(100),
    policy_number VARCHAR(100) NOT NULL,
    line_of_business VARCHAR(100) NOT NULL,
    carrier VARCHAR(200) NOT NULL,
    premium DECIMAL(14,2),
    effective_date DATE,
    expiration_date DATE,
    status VARCHAR(50),
        -- Active, Cancelled, Expired, Pending Cancel, Renewed
    ams_lock_state VARCHAR(50),
        -- mirrors Espo Policy.amsLockState: Pending Sync, Synced, NULL
    coverage_limits JSONB,
    deductibles JSONB,
    mortgagees JSONB,
    additional_insureds JSONB,
    source VARCHAR(500),
    source_ref VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT policy_facts_unique UNIQUE (policy_number, carrier)
);

CREATE INDEX IF NOT EXISTS idx_policy_facts_entity
    ON public.policy_facts (entity_id);
CREATE INDEX IF NOT EXISTS idx_policy_facts_policy_number
    ON public.policy_facts (policy_number);
CREATE INDEX IF NOT EXISTS idx_policy_facts_expiration
    ON public.policy_facts (expiration_date);
CREATE INDEX IF NOT EXISTS idx_policy_facts_crm_policy
    ON public.policy_facts (crm_policy_id) WHERE crm_policy_id IS NOT NULL;

-- -------------------------------------------------------------------------------------
-- underwriting_facts — risk/exposure facts that drive carrier appetite
-- (class codes, payroll, vehicle counts, exposures, loss history)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.underwriting_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.client_entities(id) ON DELETE CASCADE,
    line_of_business VARCHAR(100),
    fact_label VARCHAR(100) NOT NULL,
        -- WC Class Code, ISO GL Class, NAICS, Payroll, Vehicle Count, Driver Count,
        -- Years In Business, Claims Last 5Y, Incurred Last 5Y, Mod, Square Footage,
        -- Building Value, BPP Value, Equipment Schedule Value, etc.
    fact_value TEXT NOT NULL,
    fact_value_numeric DECIMAL(18,4),
        -- Populated when the fact is a number (payroll, counts, etc.) for range queries.
    severity VARCHAR(20),
        -- low | medium | high | critical (for flags like adverse losses, pollution exposure)
    sensitivity retrieval_sensitivity NOT NULL DEFAULT 'standard',
    confidence retrieval_confidence NOT NULL DEFAULT 'high',
    source VARCHAR(500) NOT NULL,
    source_ref VARCHAR(500),
    as_of_date DATE,
    superseded_at TIMESTAMPTZ,
    superseded_by UUID REFERENCES public.underwriting_facts(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT underwriting_facts_severity_check CHECK (
        severity IS NULL OR severity IN ('low', 'medium', 'high', 'critical')
    )
);

CREATE INDEX IF NOT EXISTS idx_underwriting_facts_entity_lob_label
    ON public.underwriting_facts (entity_id, line_of_business, fact_label);
CREATE INDEX IF NOT EXISTS idx_underwriting_facts_label_active
    ON public.underwriting_facts (fact_label) WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_underwriting_facts_numeric_active
    ON public.underwriting_facts (fact_label, fact_value_numeric)
    WHERE fact_value_numeric IS NOT NULL AND superseded_at IS NULL;

-- -------------------------------------------------------------------------------------
-- updated_at triggers (reuse public.hermes_touch_updated_at from edge_cases_hardening)
-- -------------------------------------------------------------------------------------
DROP TRIGGER IF EXISTS hermes_touch_updated_at_client_entities ON public.client_entities;
CREATE TRIGGER hermes_touch_updated_at_client_entities
    BEFORE UPDATE ON public.client_entities
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_client_relationships ON public.client_relationships;
CREATE TRIGGER hermes_touch_updated_at_client_relationships
    BEFORE UPDATE ON public.client_relationships
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_client_facts ON public.client_facts;
CREATE TRIGGER hermes_touch_updated_at_client_facts
    BEFORE UPDATE ON public.client_facts
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_client_notes ON public.client_notes;
CREATE TRIGGER hermes_touch_updated_at_client_notes
    BEFORE UPDATE ON public.client_notes
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_client_documents ON public.client_documents;
CREATE TRIGGER hermes_touch_updated_at_client_documents
    BEFORE UPDATE ON public.client_documents
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_quote_facts ON public.quote_facts;
CREATE TRIGGER hermes_touch_updated_at_quote_facts
    BEFORE UPDATE ON public.quote_facts
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_policy_facts ON public.policy_facts;
CREATE TRIGGER hermes_touch_updated_at_policy_facts
    BEFORE UPDATE ON public.policy_facts
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_underwriting_facts ON public.underwriting_facts;
CREATE TRIGGER hermes_touch_updated_at_underwriting_facts
    BEFORE UPDATE ON public.underwriting_facts
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

-- -------------------------------------------------------------------------------------
-- RLS — service_role full access, authenticated SELECT on non-sensitive shapes
-- -------------------------------------------------------------------------------------
ALTER TABLE public.client_entities       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_relationships  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_facts          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_notes          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_documents      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.quote_facts           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.policy_facts          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.underwriting_facts    ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.client_entities;
CREATE POLICY "Service Role Full Access" ON public.client_entities
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.client_relationships;
CREATE POLICY "Service Role Full Access" ON public.client_relationships
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.client_facts;
CREATE POLICY "Service Role Full Access" ON public.client_facts
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.client_notes;
CREATE POLICY "Service Role Full Access" ON public.client_notes
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.client_documents;
CREATE POLICY "Service Role Full Access" ON public.client_documents
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.quote_facts;
CREATE POLICY "Service Role Full Access" ON public.quote_facts
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.policy_facts;
CREATE POLICY "Service Role Full Access" ON public.policy_facts
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.underwriting_facts;
CREATE POLICY "Service Role Full Access" ON public.underwriting_facts
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Authenticated dashboard readers: read entities + relationships + notes + docs + quote/policy facts.
-- Restricted-tier client_facts and underwriting_facts are intentionally NOT exposed to authenticated
-- — Hermes (service_role) is the only path to read those.
DROP POLICY IF EXISTS "authenticated_select_client_entities" ON public.client_entities;
CREATE POLICY "authenticated_select_client_entities" ON public.client_entities
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "authenticated_select_client_relationships" ON public.client_relationships;
CREATE POLICY "authenticated_select_client_relationships" ON public.client_relationships
    FOR SELECT TO authenticated USING (sensitivity = 'standard');

DROP POLICY IF EXISTS "authenticated_select_client_facts" ON public.client_facts;
CREATE POLICY "authenticated_select_client_facts" ON public.client_facts
    FOR SELECT TO authenticated USING (sensitivity = 'standard' AND superseded_at IS NULL);

DROP POLICY IF EXISTS "authenticated_select_client_notes" ON public.client_notes;
CREATE POLICY "authenticated_select_client_notes" ON public.client_notes
    FOR SELECT TO authenticated USING (sensitivity = 'standard' AND audience = 'client_safe');

DROP POLICY IF EXISTS "authenticated_select_client_documents" ON public.client_documents;
CREATE POLICY "authenticated_select_client_documents" ON public.client_documents
    FOR SELECT TO authenticated USING (sensitivity = 'standard');

DROP POLICY IF EXISTS "authenticated_select_quote_facts" ON public.quote_facts;
CREATE POLICY "authenticated_select_quote_facts" ON public.quote_facts
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "authenticated_select_policy_facts" ON public.policy_facts;
CREATE POLICY "authenticated_select_policy_facts" ON public.policy_facts
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "authenticated_select_underwriting_facts" ON public.underwriting_facts;
CREATE POLICY "authenticated_select_underwriting_facts" ON public.underwriting_facts
    FOR SELECT TO authenticated USING (sensitivity = 'standard' AND superseded_at IS NULL);

-- -------------------------------------------------------------------------------------
-- Comments — surface intent for psql + Supabase studio
-- -------------------------------------------------------------------------------------
COMMENT ON TABLE  public.client_entities       IS 'Canonical retrieval index for any entity (Account/Contact/Opportunity/Policy/Renewal/Lead/Household). Backs the agency-memory layer.';
COMMENT ON TABLE  public.client_relationships  IS 'Person-to-entity links (Principal, Spouse, Beneficiary, Decision Maker, etc.).';
COMMENT ON TABLE  public.client_facts          IS 'Structured key/value facts (EIN, phone, DOB, revenue, payroll). Source of truth for crm-fact-retriever.';
COMMENT ON TABLE  public.client_notes          IS 'Structured narrative notes paired with EspoCRM ClientNote rows. Body produced by the crm-note-structurer skill.';
COMMENT ON TABLE  public.client_documents      IS 'Document references (PDFs, emails, transcripts) with extracted-text + summary.';
COMMENT ON TABLE  public.quote_facts           IS 'Per-quote financial detail — one row per quote line. Powers quote retrieval and proposal-builder.';
COMMENT ON TABLE  public.policy_facts          IS 'Per-policy detail for bound coverage. Mirrors key Espo Policy fields for fast retrieval.';
COMMENT ON TABLE  public.underwriting_facts    IS 'Risk/exposure facts that drive carrier appetite (class codes, payroll, exposures, losses).';
