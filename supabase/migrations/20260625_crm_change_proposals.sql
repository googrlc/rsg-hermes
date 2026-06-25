-- =====================================================================================
-- CRM CHANGE PROPOSALS
-- Staging table for proposed EspoCRM field updates awaiting human approval.
-- An agent (or Gretchen) proposes a change (status=pending); a reviewer approves
-- from chat; the approve endpoint enqueues one crm_write_queue row, which the
-- hermes-crm-queue-worker commits to EspoCRM. Nothing bypasses the review gate.
-- Consumed by hermes/operations/crm_proposals.py + the /api/crm/proposals routes.
-- Idempotent: the table was created out-of-band; this anchors the schema in the repo.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.crm_change_proposals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity       TEXT NOT NULL,                 -- EspoCRM entity: Account | Contact | Opportunity | ...
    match_key    TEXT,                           -- how the target was identified (name/email/fein/...)
    espocrm_id   TEXT,                           -- target record id; NULL for op='create'
    op           TEXT NOT NULL DEFAULT 'upsert',  -- upsert | create | update
    before       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- current field values (audit/snapshot)
    after        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- proposed EspoCRM field dict (EspoCRM field names)
    rationale    TEXT,
    confidence   NUMERIC,
    source       TEXT,                           -- agent label / n8n workflow / slack ts / ...
    status       TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected | committed | failed
    proposed_by  TEXT NOT NULL DEFAULT 'agent',  -- claude | hermes | gretchen | other
    reviewed_by  TEXT,
    committed_at TIMESTAMPTZ,
    result       JSONB,                           -- {queue_id, ...} on approve; worker outcome on commit
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT crm_change_proposals_status_check
        CHECK (status IN ('pending','approved','rejected','committed','failed'))
);

CREATE INDEX IF NOT EXISTS idx_crm_change_proposals_status
    ON public.crm_change_proposals (status);
CREATE INDEX IF NOT EXISTS idx_crm_change_proposals_pending
    ON public.crm_change_proposals (created_at DESC) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_crm_change_proposals_espocrm
    ON public.crm_change_proposals (entity, espocrm_id);

DROP TRIGGER IF EXISTS hermes_touch_updated_at_crm_change_proposals
    ON public.crm_change_proposals;
CREATE TRIGGER hermes_touch_updated_at_crm_change_proposals
    BEFORE UPDATE ON public.crm_change_proposals
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

ALTER TABLE public.crm_change_proposals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.crm_change_proposals;
CREATE POLICY "Service Role Full Access" ON public.crm_change_proposals
    FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.crm_change_proposals IS
    'Staging table for proposed EspoCRM field updates. Approve enqueues a crm_write_queue row; the hermes-crm-queue-worker commits to EspoCRM. See hermes/operations/crm_proposals.py.';
