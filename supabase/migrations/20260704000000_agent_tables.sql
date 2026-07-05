-- RSG Agent System — agent audit + hygiene tables.
-- Companion to 00_shared_standards.md (agent lifecycle, blast-radius, dry-run, audit mirror).
-- Pattern follows 20260507010000_sync_control_tables.sql (RLS + service-role full access).

-- 1. agent_runs: one row per agent execution (cron / webhook / on-demand)
CREATE TABLE IF NOT EXISTS public.agent_runs (
    run_id              TEXT PRIMARY KEY,              -- ULID
    agent_name          TEXT NOT NULL,
    trigger_source      TEXT NOT NULL DEFAULT 'on-demand',  -- cron|webhook|on-demand
    state               TEXT NOT NULL DEFAULT 'dry_run',   -- dry_run|shadow|live_supervised|live_autonomous|paused|error
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    writes_attempted    INTEGER NOT NULL DEFAULT 0,
    writes_executed     INTEGER NOT NULL DEFAULT 0,
    writes_skipped      INTEGER NOT NULL DEFAULT 0,
    escalations         INTEGER NOT NULL DEFAULT 0,
    dry_run             BOOLEAN NOT NULL DEFAULT true,
    summary             TEXT,
    error               TEXT,
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_started
    ON public.agent_runs (agent_name, started_at DESC);

-- 2. agent_writes: every tool call that mutates the AMS, mirrored here for audit + rollback
CREATE TABLE IF NOT EXISTS public.agent_writes (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              TEXT REFERENCES public.agent_runs(run_id) ON DELETE CASCADE,
    agent_name          TEXT NOT NULL,
    target_system       TEXT NOT NULL DEFAULT 'momentum',  -- momentum|espocrm|supabase|quickbooks
    tool_name           TEXT NOT NULL,
    target_entity       TEXT,                              -- insured|policy|opportunity|task|note|certificate|tag
    target_id           TEXT,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,  -- request args (PII-redacted)
    response            JSONB NOT NULL DEFAULT '{}'::jsonb,  -- tool response summary
    status              TEXT NOT NULL DEFAULT 'pending',     -- pending|executed|skipped|escalated|failed|rolled_back
    dry_run             BOOLEAN NOT NULL DEFAULT true,
    reversible_until    TIMESTAMPTZ,                        -- 7-day rollback window per shared standard 3.6
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_writes_run ON public.agent_writes (run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_writes_target ON public.agent_writes (target_system, target_entity, target_id);
CREATE INDEX IF NOT EXISTS idx_agent_writes_rollback ON public.agent_writes (status, reversible_until)
    WHERE status = 'executed';

-- 3. book_hygiene_findings: one row per issue detected by Agent 01 (Book Hygiene Auditor)
CREATE TABLE IF NOT EXISTS public.book_hygiene_findings (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              TEXT REFERENCES public.agent_runs(run_id) ON DELETE SET NULL,
    insured_database_id TEXT,
    finding_type        TEXT NOT NULL,   -- duplicate|missing_field|orphan_policy|stale|tag_conflict
    severity            TEXT NOT NULL DEFAULT 'medium',  -- low|medium|high
    confidence          NUMERIC(4,3) NOT NULL DEFAULT 0.0,
    details             JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'open',   -- open|reviewed|resolved|ignored|false_positive
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at         TIMESTAMPTZ,
    resolved_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bhf_type_status ON public.book_hygiene_findings (finding_type, status);
CREATE INDEX IF NOT EXISTS idx_bhf_insured ON public.book_hygiene_findings (insured_database_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bhf_dedupe
    ON public.book_hygiene_findings (run_id, insured_database_id, finding_type);

-- updated_at touch triggers
CREATE OR REPLACE FUNCTION public.touch_updated_at() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

ALTER TABLE public.agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_writes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.book_hygiene_findings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.agent_runs;
CREATE POLICY "Service Role Full Access" ON public.agent_runs
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.agent_writes;
CREATE POLICY "Service Role Full Access" ON public.agent_writes
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service Role Full Access" ON public.book_hygiene_findings;
CREATE POLICY "Service Role Full Access" ON public.book_hygiene_findings
    FOR ALL TO service_role USING (true) WITH CHECK (true);
