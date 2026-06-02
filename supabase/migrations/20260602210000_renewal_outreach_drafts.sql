-- =====================================================================================
-- RENEWAL OUTREACH DRAFTS  (Command Center, Phase 2 — Retention save-list)
-- Purpose: durable staging for renewal save-list outreach drafts. The Command Center
-- "Build the save-list" action writes DRAFT rows here; nothing is ever auto-sent —
-- sending stays a manual, human step.
-- Consumed by hermes/operations/save_list.py + the /api/command-center/save-list endpoints.
-- Server-only table: RLS is ENABLED with NO policies, so the anon/authenticated roles
-- are fully blocked; hermes-api reaches it via the service-role key (which bypasses RLS).
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.renewal_outreach_drafts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    renewal_id       UUID REFERENCES public.project_85_renewals(id) ON DELETE SET NULL,
    batch_id         UUID,
    policy_number    TEXT,
    client_name      TEXT,
    line_of_business TEXT,
    expiration_date  DATE,
    days_until       INTEGER,
    premium_current  NUMERIC(12,2),
    risk_status      TEXT,
    channel          TEXT NOT NULL DEFAULT 'email' CHECK (channel IN ('email', 'call', 'text')),
    subject          TEXT,
    body             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'APPROVED', 'SENT', 'DISCARDED')),
    created_by       TEXT DEFAULT 'command-center',
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outreach_drafts_status ON public.renewal_outreach_drafts(status);
CREATE INDEX IF NOT EXISTS idx_outreach_drafts_batch  ON public.renewal_outreach_drafts(batch_id);

ALTER TABLE public.renewal_outreach_drafts ENABLE ROW LEVEL SECURITY;
