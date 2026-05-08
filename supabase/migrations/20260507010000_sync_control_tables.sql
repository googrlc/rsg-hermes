-- SYNC CONTROL TABLES — NowCerts ↔ EspoCRM pipeline spine
-- NOTE: If the remote DB already has `create_sync_control_foundation` with a different
-- `inbound_sync_staging` shape, apply `20260507015000_sync_schema_alignment.sql` first,
-- then use `20260507021000_*` / `20260507022000_*` for RLS + extra triggers instead of
-- re-running this whole file verbatim.
-- Purpose: staging, identity mapping, outbound queue, audit trail, error/conflict tracking
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- 1. ENUMS
-- -------------------------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE sync_direction AS ENUM ('nowcerts_to_espocrm', 'espocrm_to_nowcerts');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE sync_run_status AS ENUM ('running', 'success', 'failed', 'partial');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE processing_status AS ENUM ('pending', 'processing', 'mapped', 'queued', 'skipped', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE queue_action AS ENUM ('create', 'update', 'skip');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE queue_status AS ENUM ('queued', 'processing', 'completed', 'failed', 'needs_approval');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE match_method AS ENUM ('dedup_key', 'fein', 'email', 'name_match', 'policy_number', 'manual', 'none');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE audit_action AS ENUM ('create', 'update', 'skip', 'error', 'conflict');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE conflict_resolution AS ENUM ('pending', 'source_wins', 'dest_wins', 'manual', 'ignored');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- -------------------------------------------------------------------------------------
-- 2. SYNC RUNS — one row per pipeline execution
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sync_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_name   VARCHAR(100) NOT NULL,
    source_system   VARCHAR(50)  NOT NULL DEFAULT 'nowcerts',
    destination_system VARCHAR(50) NOT NULL DEFAULT 'espocrm',
    direction       sync_direction NOT NULL DEFAULT 'nowcerts_to_espocrm',
    status          sync_run_status NOT NULL DEFAULT 'running',
    records_processed INT DEFAULT 0,
    records_created INT DEFAULT 0,
    records_updated INT DEFAULT 0,
    records_skipped INT DEFAULT 0,
    records_failed  INT DEFAULT 0,
    error_summary   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- -------------------------------------------------------------------------------------
-- 3. INBOUND SYNC STAGING — raw payloads from source system
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.inbound_sync_staging (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID REFERENCES public.sync_runs(id) ON DELETE CASCADE,
    source_system       VARCHAR(50)  NOT NULL DEFAULT 'nowcerts',
    source_object_type  VARCHAR(100) NOT NULL,
    source_object_id    VARCHAR(255) NOT NULL,
    raw_payload         JSONB        NOT NULL,
    payload_hash        VARCHAR(64),
    processing_status   processing_status NOT NULL DEFAULT 'pending',
    error_message       TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_staging_run_source
    ON public.inbound_sync_staging (run_id, source_system, source_object_type, source_object_id);

-- -------------------------------------------------------------------------------------
-- 4. SYNC MAPPINGS — identity crosswalk between systems
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sync_mappings (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nowcerts_entity_type    VARCHAR(100) NOT NULL,
    nowcerts_id             VARCHAR(255) NOT NULL,
    espocrm_entity_type     VARCHAR(100) NOT NULL,
    espocrm_id              VARCHAR(255),
    match_method            match_method NOT NULL DEFAULT 'none',
    match_confidence        DECIMAL(3,2) DEFAULT 0.00,
    active                  BOOLEAN DEFAULT true,
    last_synced_at          TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_mappings_nc
    ON public.sync_mappings (nowcerts_entity_type, nowcerts_id);

CREATE INDEX IF NOT EXISTS idx_sync_mappings_espo
    ON public.sync_mappings (espocrm_entity_type, espocrm_id)
    WHERE espocrm_id IS NOT NULL;

-- -------------------------------------------------------------------------------------
-- 5. OUTBOUND SYNC QUEUE — staged writes to destination system
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.outbound_sync_queue (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID REFERENCES public.sync_runs(id) ON DELETE SET NULL,
    mapping_id          UUID REFERENCES public.sync_mappings(id) ON DELETE SET NULL,
    object_type         VARCHAR(100) NOT NULL,
    object_id           VARCHAR(255),
    destination_system  VARCHAR(50)  NOT NULL DEFAULT 'espocrm',
    action              queue_action NOT NULL DEFAULT 'create',
    payload             JSONB        NOT NULL,
    status              queue_status NOT NULL DEFAULT 'queued',
    attempt_count       INT DEFAULT 0,
    last_error          TEXT,
    approved_by         VARCHAR(255),
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outbound_queue_status
    ON public.outbound_sync_queue (status, created_at)
    WHERE status IN ('queued', 'processing', 'failed');

-- -------------------------------------------------------------------------------------
-- 6. SYNC AUDIT LOG — every logical record touched
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sync_audit_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID REFERENCES public.sync_runs(id) ON DELETE SET NULL,
    object_type         VARCHAR(100) NOT NULL,
    source_object_id    VARCHAR(255),
    dest_object_id      VARCHAR(255),
    action              audit_action NOT NULL,
    status              VARCHAR(50)  NOT NULL DEFAULT 'success',
    before_snapshot     JSONB,
    after_snapshot      JSONB,
    payload_hash        VARCHAR(64),
    message             TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_run
    ON public.sync_audit_log (run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_object
    ON public.sync_audit_log (object_type, source_object_id);

-- -------------------------------------------------------------------------------------
-- 7. SYNC ERRORS — detailed error tracking
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sync_errors (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID REFERENCES public.sync_runs(id) ON DELETE SET NULL,
    staging_id          UUID REFERENCES public.inbound_sync_staging(id) ON DELETE SET NULL,
    queue_id            UUID REFERENCES public.outbound_sync_queue(id) ON DELETE SET NULL,
    object_type         VARCHAR(100) NOT NULL,
    source_object_id    VARCHAR(255),
    error_code          VARCHAR(50),
    error_message       TEXT NOT NULL,
    error_detail        JSONB,
    retryable           BOOLEAN DEFAULT true,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sync_errors_run
    ON public.sync_errors (run_id);

-- -------------------------------------------------------------------------------------
-- 8. SYNC CONFLICTS — field-level disagreements needing review
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sync_conflicts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID REFERENCES public.sync_runs(id) ON DELETE SET NULL,
    mapping_id          UUID REFERENCES public.sync_mappings(id) ON DELETE SET NULL,
    object_type         VARCHAR(100) NOT NULL,
    source_object_id    VARCHAR(255) NOT NULL,
    dest_object_id      VARCHAR(255) NOT NULL,
    field_name          VARCHAR(255) NOT NULL,
    source_value        TEXT,
    dest_value          TEXT,
    resolution          conflict_resolution NOT NULL DEFAULT 'pending',
    resolved_by         VARCHAR(255),
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sync_conflicts_pending
    ON public.sync_conflicts (resolution)
    WHERE resolution = 'pending';

-- -------------------------------------------------------------------------------------
-- 9. AUTO-UPDATED_AT TRIGGERS
-- -------------------------------------------------------------------------------------
DROP TRIGGER IF EXISTS hermes_touch_updated_at_inbound_sync_staging ON public.inbound_sync_staging;
CREATE TRIGGER hermes_touch_updated_at_inbound_sync_staging
  BEFORE UPDATE ON public.inbound_sync_staging
  FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_sync_mappings ON public.sync_mappings;
CREATE TRIGGER hermes_touch_updated_at_sync_mappings
  BEFORE UPDATE ON public.sync_mappings
  FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_outbound_sync_queue ON public.outbound_sync_queue;
CREATE TRIGGER hermes_touch_updated_at_outbound_sync_queue
  BEFORE UPDATE ON public.outbound_sync_queue
  FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

-- -------------------------------------------------------------------------------------
-- 10. ROW LEVEL SECURITY
-- -------------------------------------------------------------------------------------
ALTER TABLE public.sync_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inbound_sync_staging ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbound_sync_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_errors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_conflicts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service Role Full Access" ON public.sync_runs
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.inbound_sync_staging
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.sync_mappings
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.outbound_sync_queue
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.sync_audit_log
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.sync_errors
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.sync_conflicts
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Authenticated users can view sync state (read-only dashboard access)
CREATE POLICY "authenticated_select_sync_runs" ON public.sync_runs
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_sync_mappings" ON public.sync_mappings
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_sync_audit_log" ON public.sync_audit_log
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_sync_conflicts" ON public.sync_conflicts
  FOR SELECT TO authenticated USING (true);
