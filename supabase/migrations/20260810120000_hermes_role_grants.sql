-- Hermes process roles for the write-routing split.
--
-- hermes_write     → HERMES_ROLE=write_in (NowCerts core). Full CRUD on the
--                    AMS mirror and the write spine (queue + portal_write_log).
-- hermes_finance   → HERMES_ROLE=finance_readout. SELECT-only on the mirror /
--                    commission tables finance needs. Explicit REVOKE on the
--                    AMS write spine so a misconfigured finance process cannot
--                    enqueue or audit AMS pushes.
--
-- Prefer pointing the finance DATABASE_URL at a read replica when one exists.
-- These roles are NOLOGIN; grant them to the login roles your deploy uses.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hermes_write') THEN
    CREATE ROLE hermes_write NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hermes_finance') THEN
    CREATE ROLE hermes_finance NOLOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO hermes_write;
GRANT USAGE ON SCHEMA public TO hermes_finance;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
  public.outbound_sync_queue,
  public.portal_write_log,
  public.portal_overrides,
  public.canonical_clients,
  public.canonical_policies
TO hermes_write;

GRANT SELECT ON TABLE
  public.commission_ledger,
  public.commission_audits,
  public.portal_overrides,
  public.canonical_clients,
  public.canonical_policies
TO hermes_finance;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'commission_rules'
  ) THEN
    EXECUTE 'GRANT SELECT ON TABLE public.commission_rules TO hermes_finance';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.commission_rules TO hermes_write';
  END IF;
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'commission_ingest_batches'
  ) THEN
    EXECUTE 'GRANT SELECT ON TABLE public.commission_ingest_batches TO hermes_finance';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.commission_ingest_batches TO hermes_write';
  END IF;
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'commission_statement_staging'
  ) THEN
    EXECUTE 'GRANT SELECT ON TABLE public.commission_statement_staging TO hermes_finance';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.commission_statement_staging TO hermes_write';
  END IF;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
  public.commission_ledger,
  public.commission_audits
TO hermes_write;

REVOKE ALL ON TABLE public.outbound_sync_queue FROM hermes_finance;
REVOKE ALL ON TABLE public.portal_write_log FROM hermes_finance;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.views
    WHERE table_schema = 'public' AND table_name = 'failed_pushes'
  ) THEN
    EXECUTE 'REVOKE ALL ON TABLE public.failed_pushes FROM hermes_finance';
  END IF;
END
$$;

COMMENT ON ROLE hermes_write IS
  'HERMES_ROLE=write_in — NowCerts core; CRUD on mirror + outbound_sync_queue + portal_write_log';
COMMENT ON ROLE hermes_finance IS
  'HERMES_ROLE=finance_readout — SELECT on commission/mirror only; no AMS write spine. Prefer read replica.';
