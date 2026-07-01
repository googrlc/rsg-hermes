-- =====================================================================================
-- RENEWAL LOOP V6
-- Purpose: append-mostly ledger for EspoCRM renewal disposition / worksheet events and
-- Momentum MCP notes-only writeback tracking. Service-role only; Hermes writes first to
-- Supabase before any downstream AMS call.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.renewals_master (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    renewal_id                TEXT NOT NULL UNIQUE,
    account_id                TEXT,
    account_name              TEXT,
    line_of_business          TEXT,
    expiration_date           DATE,
    pipeline_stage            TEXT,
    disposition               TEXT,
    current_premium           NUMERIC(12,2),
    renewal_proposed_premium  NUMERIC(12,2),
    renewal_premium           NUMERIC(12,2),
    premium_change            NUMERIC(12,2),
    carrier_premium_change    NUMERIC(12,2),
    worksheet_id              TEXT,
    worksheet_lob_variant     TEXT,
    completion_type           TEXT,
    source_payload            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.renewal_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_uuid    TEXT NOT NULL UNIQUE,
    renewal_id    TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    source_system TEXT NOT NULL DEFAULT 'espocrm',
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.crm_sync_log (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_uuid         TEXT NOT NULL UNIQUE,
    renewal_id         TEXT NOT NULL,
    event_type         TEXT NOT NULL,
    source_system      TEXT NOT NULL DEFAULT 'espocrm',
    destination_system TEXT NOT NULL DEFAULT 'supabase',
    status             TEXT NOT NULL DEFAULT 'received',
    message            TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.crm_dispositions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_uuid      TEXT NOT NULL UNIQUE,
    renewal_id      TEXT NOT NULL,
    pipeline_stage  TEXT,
    disposition     TEXT,
    worksheet_id    TEXT,
    completion_type TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ams_writeback_log (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_uuid       TEXT NOT NULL UNIQUE,
    renewal_id       TEXT NOT NULL,
    target           TEXT NOT NULL DEFAULT 'manage_notes',
    state            TEXT NOT NULL DEFAULT 'pending',
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_retry_at    TIMESTAMPTZ,
    last_error       TEXT,
    payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB,
    posted_note_id   TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_renewal_events_renewal_id ON public.renewal_events(renewal_id);
CREATE INDEX IF NOT EXISTS idx_crm_sync_log_renewal_id ON public.crm_sync_log(renewal_id);
CREATE INDEX IF NOT EXISTS idx_crm_dispositions_renewal_id ON public.crm_dispositions(renewal_id);
CREATE INDEX IF NOT EXISTS idx_ams_writeback_log_state_retry ON public.ams_writeback_log(state, next_retry_at);

ALTER TABLE public.renewals_master ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.renewal_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_sync_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_dispositions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ams_writeback_log ENABLE ROW LEVEL SECURITY;
