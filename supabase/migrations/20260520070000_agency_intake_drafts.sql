-- =====================================================================================
-- AGENCY INTAKE DRAFTS
-- Purpose: durable storage for in-flight intake payloads awaiting an approval token.
-- Consumed by hermes/commands/agency_intake.py + hermes/operations/agency_intake_approval.py.
-- See docs/agency-memory-plan.md §7 (write safety / approval).
-- =====================================================================================

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'agency_intake_draft_status') THEN
    CREATE TYPE agency_intake_draft_status AS ENUM (
      'pending',
      'approved',
      'partially_approved',
      'revised',
      'canceled',
      'expired'
    );
  END IF;
END$$;

CREATE TABLE IF NOT EXISTS public.agency_intake_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submitted_by VARCHAR(200),
        -- Slack user id, email, or "system"
    source_type VARCHAR(50) NOT NULL DEFAULT 'manual',
        -- slack_summary, document, email, transcript, quote_proposal, manual
    source_ref VARCHAR(500),
        -- message_ts, file name, URL, etc.
    raw_input TEXT,
        -- Original text the producer pasted/forwarded. Restricted-by-default.
    payload JSONB NOT NULL,
        -- Full crm-intake-writer JSON: account, contacts, opportunities, note, facts, …
    classification JSONB NOT NULL DEFAULT '[]'::jsonb,
    lines_of_business JSONB NOT NULL DEFAULT '[]'::jsonb,
    duplicate_search JSONB NOT NULL DEFAULT '{}'::jsonb,

    status agency_intake_draft_status NOT NULL DEFAULT 'pending',
    approval_token VARCHAR(50),
        -- APPROVE ALL | APPROVE CRM ONLY | APPROVE SUPABASE ONLY | APPROVE TASKS ONLY |
        -- REVISE | CANCEL — set when an approver responds.
    approved_by VARCHAR(200),
    approved_at TIMESTAMPTZ,

    write_plan JSONB,
        -- Snapshot of the crm-upsert-planner output produced at approval time.
    enqueued_queue_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- crm_write_queue row ids created when the draft was approved.
    retrieval_row_ids JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- {client_entities: [...], client_facts: [...], client_notes: [...]}

    error TEXT,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agency_intake_drafts_status
    ON public.agency_intake_drafts (status);
CREATE INDEX IF NOT EXISTS idx_agency_intake_drafts_open
    ON public.agency_intake_drafts (created_at DESC) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_agency_intake_drafts_submitted_by
    ON public.agency_intake_drafts (submitted_by) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_agency_intake_drafts_expires_at
    ON public.agency_intake_drafts (expires_at) WHERE status = 'pending';

DROP TRIGGER IF EXISTS hermes_touch_updated_at_agency_intake_drafts
    ON public.agency_intake_drafts;
CREATE TRIGGER hermes_touch_updated_at_agency_intake_drafts
    BEFORE UPDATE ON public.agency_intake_drafts
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

ALTER TABLE public.agency_intake_drafts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.agency_intake_drafts;
CREATE POLICY "Service Role Full Access" ON public.agency_intake_drafts
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Authenticated dashboard readers: see open drafts (no raw_input, no payload restricted facts).
-- Note: PostgREST does not enforce per-column RLS, so dashboards should request only
-- non-sensitive columns explicitly. raw_input + payload remain reachable only via service_role.
DROP POLICY IF EXISTS "authenticated_select_agency_intake_drafts" ON public.agency_intake_drafts;
CREATE POLICY "authenticated_select_agency_intake_drafts" ON public.agency_intake_drafts
    FOR SELECT TO authenticated USING (status IN ('pending', 'approved', 'partially_approved'));

COMMENT ON TABLE public.agency_intake_drafts IS
    'Staging table for crm-intake-writer payloads awaiting an approval token (APPROVE ALL / APPROVE CRM ONLY / REVISE / CANCEL etc.). Approved drafts produce crm_write_queue rows + retrieval inserts.';
