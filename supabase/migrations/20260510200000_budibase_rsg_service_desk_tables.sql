-- RSG Operations Console — core tables for Service Desk, AI review, and carrier appetite.
-- RLS still protects PostgREST/anon paths.

-- -------------------------------------------------------------------------------------
-- service_requests — RSG Service Desk queue
-- -------------------------------------------------------------------------------------
CREATE TABLE public.service_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_type text NOT NULL,
  status text NOT NULL DEFAULT 'Inbox',
  priority text NOT NULL DEFAULT 'Normal',
  account_id uuid,
  account_name text,
  named_insured text,
  policy_id uuid,
  policy_number text,
  carrier text,
  line_of_business text,
  due_date date,
  assigned_to text,
  description text,
  client_email text,
  files_links jsonb DEFAULT '[]'::jsonb,
  ai_summary text,
  ai_suggested_action text,
  source text,
  created_by text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX service_requests_status_idx ON public.service_requests (status);
CREATE INDEX service_requests_assigned_to_idx ON public.service_requests (assigned_to);
CREATE INDEX service_requests_due_date_idx ON public.service_requests (due_date);
CREATE INDEX service_requests_created_at_idx ON public.service_requests (created_at DESC);

DROP TRIGGER IF EXISTS hermes_touch_updated_at_service_requests ON public.service_requests;
CREATE TRIGGER hermes_touch_updated_at_service_requests
  BEFORE UPDATE ON public.service_requests
  FOR EACH ROW
  EXECUTE FUNCTION public.hermes_touch_updated_at();

ALTER TABLE public.service_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.service_requests;
CREATE POLICY "Service Role Full Access" ON public.service_requests
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- -------------------------------------------------------------------------------------
-- ai_review_queue — human approval before anything pushes to CRM / AMS
-- -------------------------------------------------------------------------------------
CREATE TABLE public.ai_review_queue (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type text NOT NULL,
  source_id uuid,
  account_name text,
  review_type text NOT NULL,
  ai_output jsonb NOT NULL,
  confidence numeric,
  status text DEFAULT 'Needs Review',
  reviewer text,
  reviewed_at timestamptz,
  approved_payload jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX ai_review_queue_status_idx ON public.ai_review_queue (status);
CREATE INDEX ai_review_queue_review_type_idx ON public.ai_review_queue (review_type);
CREATE INDEX ai_review_queue_created_at_idx ON public.ai_review_queue (created_at DESC);

ALTER TABLE public.ai_review_queue ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.ai_review_queue;
CREATE POLICY "Service Role Full Access" ON public.ai_review_queue
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- -------------------------------------------------------------------------------------
-- carrier_appetite — operator-maintained appetite grid
-- -------------------------------------------------------------------------------------
CREATE TABLE public.carrier_appetite (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  carrier text NOT NULL,
  state text,
  line_of_business text,
  class_description text,
  naics_code text,
  sic_code text,
  gl_class_code text,
  wc_class_code text,
  appetite_level text,
  notes text,
  commission_percent numeric,
  source text,
  last_verified date,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX carrier_appetite_carrier_state_idx ON public.carrier_appetite (carrier, state);

DROP TRIGGER IF EXISTS hermes_touch_updated_at_carrier_appetite ON public.carrier_appetite;
CREATE TRIGGER hermes_touch_updated_at_carrier_appetite
  BEFORE UPDATE ON public.carrier_appetite
  FOR EACH ROW
  EXECUTE FUNCTION public.hermes_touch_updated_at();

ALTER TABLE public.carrier_appetite ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service Role Full Access" ON public.carrier_appetite;
CREATE POLICY "Service Role Full Access" ON public.carrier_appetite
  FOR ALL TO service_role USING (true) WITH CHECK (true);
