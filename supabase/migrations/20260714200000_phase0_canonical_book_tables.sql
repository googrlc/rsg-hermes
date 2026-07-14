-- =====================================================================================
-- PHASE 0 BLOCKER FIX — canonical book tables + deduplication
-- 2026-07-14 audit fixes (gates v3 Renewal Walker build; see issue #97 / PR #96)
--
-- Addresses:
--   1. Create canonical tables with correct schema and UNIQUE constraints so the
--      58× insured-mirror fan-out and multi-status policy rows can never recur.
--   2. Deduplicate nowcerts_insured_mirror (6,967 rows → 120 max, 1:1 to guid).
--   3. Deduplicate canonical_clients case pairs (shamira Douglas ↔ Shamira Douglas).
--   4. Collapse canonical_policies duplicate-status rows per policy_number under
--      status-precedence: Active > Renewed > Non-Renewal/Cancelled/Lapsed > other.
--   5. Ensure agency_snapshots has a unique constraint on snapshot_date.
--
-- All statements are idempotent (IF NOT EXISTS / ON CONFLICT guards).
-- =====================================================================================

-- ─────────────────────────────────────────────────────────────────────────────────────
-- 1. nowcerts_insured_mirror
--    One row per NowCerts insured (1:1 to insured GUID).
--    The 2026-06-10 bulk load had no unique guard → ~58 rows per insured.
--    UNIQUE INDEX uq_nowcerts_insured_mirror_guid prevents that structurally.
-- ─────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.nowcerts_insured_mirror (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    insured_guid    TEXT        NOT NULL,
    commercial_name TEXT,
    first_name      TEXT,
    last_name       TEXT,
    fein            TEXT,
    insured_type    TEXT,
    active          BOOLEAN,
    raw_payload     JSONB,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nowcerts_insured_mirror_guid
    ON public.nowcerts_insured_mirror (insured_guid);

-- Dedup step: for each guid group, keep the most recently synced row.
-- First pass: delete rows where a newer synced_at exists for the same guid.
DELETE FROM public.nowcerts_insured_mirror AS n
WHERE EXISTS (
    SELECT 1
    FROM   public.nowcerts_insured_mirror AS n2
    WHERE  n2.insured_guid = n.insured_guid
      AND  (n2.synced_at > n.synced_at
            OR (n2.synced_at = n.synced_at AND n2.id > n.id))
);

-- Now apply the structural UNIQUE index (no duplicates remain at this point).
CREATE UNIQUE INDEX IF NOT EXISTS uq_nowcerts_insured_mirror_guid
    ON public.nowcerts_insured_mirror (insured_guid);

-- ─────────────────────────────────────────────────────────────────────────────────────
-- 2. canonical_clients
--    One row per RSG client. Dedup key: nowcerts_insured_guid (1:1 to insured).
--    Case duplicates (shamira Douglas / Shamira Douglas) are collapsed below.
-- ─────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.canonical_clients (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    nowcerts_insured_guid TEXT,
    espocrm_account_id    TEXT,
    name                  TEXT        NOT NULL,
    fein                  TEXT,
    account_type          TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_canonical_clients_guid
    ON public.canonical_clients (nowcerts_insured_guid);
CREATE INDEX IF NOT EXISTS idx_canonical_clients_name
    ON public.canonical_clients (name);

-- Dedup: collapse case-duplicate name pairs.
-- Strategy: for each lower(name) group with >1 row, keep the row with the
-- minimum id (deterministic; earliest UUID). Update any canonical_policies
-- FK references before deleting the loser rows.
DO $$
BEGIN
    -- Step A — re-point canonical_policies.client_id off the loser rows
    --          (only runs if the column exists — safe on a fresh DB)
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'canonical_policies'
          AND  column_name  = 'client_id'
    ) THEN
        UPDATE public.canonical_policies cp
        SET    client_id = keepers.keep_id
        FROM (
            SELECT
                LOWER(c.name)           AS lower_name,
                MIN(c.id::text)::uuid   AS keep_id
            FROM   public.canonical_clients c
            GROUP  BY LOWER(c.name)
            HAVING COUNT(*) > 1
        ) AS keepers
        JOIN public.canonical_clients c2
             ON LOWER(c2.name) = keepers.lower_name
            AND c2.id <> keepers.keep_id
        WHERE cp.client_id = c2.id;
    END IF;

    -- Step B — delete the loser rows (all but the min-id keeper per lower(name))
    DELETE FROM public.canonical_clients c
    USING (
        SELECT
            LOWER(name)           AS lower_name,
            MIN(id::text)::uuid   AS keep_id
        FROM   public.canonical_clients
        GROUP  BY LOWER(name)
        HAVING COUNT(*) > 1
    ) keepers
    WHERE LOWER(c.name) = keepers.lower_name
      AND c.id <> keepers.keep_id;
END;
$$;

-- After dedup, add UNIQUE index on nowcerts_insured_guid (non-null values only)
-- so subsequent syncs cannot create a duplicate canonical_clients row per insured.
CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_clients_insured_guid
    ON public.canonical_clients (nowcerts_insured_guid)
    WHERE nowcerts_insured_guid IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────────────
-- 3. canonical_policies
--    One current row per policy_number (latest term).
--    Status-precedence rule: Active=1, Renewed=2, Non-Renewal/Cancelled/Lapsed=3,
--    other=4. Latest expiration_date breaks ties; latest synced_at breaks further.
-- ─────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.canonical_policies (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_number         TEXT        NOT NULL,
    client_id             UUID        REFERENCES public.canonical_clients (id),
    nowcerts_insured_guid TEXT,
    line_of_business      TEXT,
    carrier               TEXT,
    status                TEXT,
    active                BOOLEAN     NOT NULL DEFAULT false,
    effective_date        DATE,
    expiration_date       DATE,
    annualized_premium    NUMERIC(12,2),
    current_term_amount   NUMERIC(12,2),
    premium_amount        NUMERIC(12,2),
    raw_payload           JSONB,
    synced_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_canonical_policies_number
    ON public.canonical_policies (policy_number);
CREATE INDEX IF NOT EXISTS idx_canonical_policies_expiry
    ON public.canonical_policies (expiration_date);

-- Dedup: for each policy_number with multiple rows, keep the one with the
-- highest-precedence status, then latest expiration_date, then latest synced_at.
DELETE FROM public.canonical_policies
WHERE id IN (
    SELECT id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY policy_number
                ORDER BY
                    CASE LOWER(COALESCE(status, ''))
                        WHEN 'active'       THEN 1
                        WHEN 'renewed'      THEN 2
                        WHEN 'non-renewal'  THEN 3
                        WHEN 'non-renewed'  THEN 3
                        WHEN 'cancelled'    THEN 3
                        WHEN 'canceled'     THEN 3
                        WHEN 'lapsed'       THEN 3
                        ELSE 4
                    END ASC,
                    expiration_date DESC NULLS LAST,
                    synced_at        DESC NULLS LAST,
                    id               DESC   -- deterministic final tiebreak
            ) AS rn
        FROM public.canonical_policies
    ) ranked
    WHERE rn > 1
);

-- After dedup, add UNIQUE index so the ingest boundary enforces 1:1 going forward.
CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_policies_number
    ON public.canonical_policies (policy_number);

-- ─────────────────────────────────────────────────────────────────────────────────────
-- 4. agency_snapshots
--    Book-level aggregates (retention rate etc.) written by book-health-monitor.
--    snapshot_date must be unique — one snapshot per calendar date.
-- ─────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.agency_snapshots (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date  DATE        NOT NULL,
    retention_rate NUMERIC(5,2),
    total_clients  INT,
    total_policies INT,
    active_premium NUMERIC(15,2),
    raw_payload    JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dedup: keep the most recently inserted snapshot per date.
DELETE FROM public.agency_snapshots
WHERE id IN (
    SELECT id
    FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY snapshot_date
                   ORDER BY created_at DESC, id DESC
               ) AS rn
        FROM public.agency_snapshots
    ) ranked
    WHERE rn > 1
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agency_snapshots_date
    ON public.agency_snapshots (snapshot_date);

-- ─────────────────────────────────────────────────────────────────────────────────────
-- 5. Row-Level Security (match existing Hermes table pattern)
-- ─────────────────────────────────────────────────────────────────────────────────────
ALTER TABLE public.nowcerts_insured_mirror ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_clients       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.canonical_policies      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agency_snapshots        ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_nowcerts_insured_mirror" ON public.nowcerts_insured_mirror;
CREATE POLICY "service_role_nowcerts_insured_mirror" ON public.nowcerts_insured_mirror
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "service_role_canonical_clients" ON public.canonical_clients;
CREATE POLICY "service_role_canonical_clients" ON public.canonical_clients
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "service_role_canonical_policies" ON public.canonical_policies;
CREATE POLICY "service_role_canonical_policies" ON public.canonical_policies
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "service_role_agency_snapshots" ON public.agency_snapshots;
CREATE POLICY "service_role_agency_snapshots" ON public.agency_snapshots
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- updated_at auto-maintenance (matches hermes_touch_updated_at pattern)
DROP TRIGGER IF EXISTS hermes_touch_updated_at_canonical_clients ON public.canonical_clients;
CREATE TRIGGER hermes_touch_updated_at_canonical_clients
    BEFORE UPDATE ON public.canonical_clients
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_canonical_policies ON public.canonical_policies;
CREATE TRIGGER hermes_touch_updated_at_canonical_policies
    BEFORE UPDATE ON public.canonical_policies
    FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();
