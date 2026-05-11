-- Budibase read access to operations console tables (Service Desk, AI review, appetite).
-- Forms that INSERT/UPDATE should use a separate Postgres role with write grants — not budibase_reader.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'budibase_reader') THEN
    CREATE ROLE budibase_reader NOLOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO budibase_reader;
GRANT SELECT ON public.service_requests TO budibase_reader;
GRANT SELECT ON public.ai_review_queue TO budibase_reader;
GRANT SELECT ON public.carrier_appetite TO budibase_reader;
