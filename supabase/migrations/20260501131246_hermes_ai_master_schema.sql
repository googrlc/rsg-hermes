-- HERMES AI OPERATIONS CENTER - SUPABASE MASTER SCHEMA
-- Purpose: Foundational database structure for the Hermes AI Operating Model
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- 1. EXTENSIONS & ENUMS
-- -------------------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TYPE sync_status AS ENUM ('PENDING', 'PROCESSING', 'SUCCESS', 'FAILED', 'BLOCKED_BY_GUARDRAIL');
CREATE TYPE commission_status AS ENUM ('PENDING', 'MATCHED', 'DISCREPANCY', 'ESCALATED', 'RECONCILED');
CREATE TYPE renewal_risk_status AS ENUM ('SAFE', 'AT_RISK', 'CRITICAL', 'RENEWED', 'LAPSED');
CREATE TYPE report_frequency AS ENUM ('DAILY', 'WEEKLY', 'MONTHLY', 'QUARTERLY');

-- -------------------------------------------------------------------------------------
-- 2. OPERATING MODEL & ROUTING (Slack & Roles)
-- -------------------------------------------------------------------------------------

CREATE TABLE public.slack_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id VARCHAR(50) UNIQUE NOT NULL,
    channel_name VARCHAR(100) NOT NULL,
    designated_purpose TEXT NOT NULL,
    allowed_ai_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.hermes_ai_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_name VARCHAR(100) UNIQUE NOT NULL,
    system_prompt_id VARCHAR(100),
    success_criteria JSONB,
    permissions JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- -------------------------------------------------------------------------------------
-- 3. CRM WRITE RULES & RECEIPTS
-- -------------------------------------------------------------------------------------

CREATE TABLE public.crm_write_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_system VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(100),
    payload JSONB NOT NULL,
    status sync_status DEFAULT 'PENDING',
    attempt_count INT DEFAULT 0,
    created_by_role VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.crm_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    queue_id UUID REFERENCES public.crm_write_queue(id) ON DELETE CASCADE,
    transaction_id VARCHAR(255) NOT NULL,
    raw_response JSONB NOT NULL,
    synced_at TIMESTAMPTZ DEFAULT now()
);

-- -------------------------------------------------------------------------------------
-- 4. COMMISSION AUDIT & RECONCILIATION
-- -------------------------------------------------------------------------------------

CREATE TABLE public.commission_audits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id VARCHAR(100) NOT NULL,
    carrier VARCHAR(100) NOT NULL,
    policy_number VARCHAR(100) NOT NULL,
    client_name VARCHAR(255),
    expected_amount DECIMAL(12,2) NOT NULL,
    received_amount DECIMAL(12,2),
    variance DECIMAL(12,2) GENERATED ALWAYS AS (received_amount - expected_amount) STORED,
    status commission_status DEFAULT 'PENDING',
    discrepancy_notes TEXT,
    snapshot_month DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.eom_scorecards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_month DATE UNIQUE NOT NULL,
    total_expected DECIMAL(15,2) NOT NULL,
    total_received DECIMAL(15,2) NOT NULL,
    total_variance DECIMAL(15,2) NOT NULL,
    discrepancy_count INT NOT NULL,
    reconciled_count INT NOT NULL,
    kpi_json JSONB NOT NULL,
    is_locked BOOLEAN DEFAULT false,
    generated_at TIMESTAMPTZ DEFAULT now()
);

-- -------------------------------------------------------------------------------------
-- 5. PROJECT 85: RENEWAL ENGINE
-- -------------------------------------------------------------------------------------

CREATE TABLE public.project_85_renewals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_number VARCHAR(100) UNIQUE NOT NULL,
    client_name VARCHAR(255) NOT NULL,
    expiration_date DATE NOT NULL,
    premium_current DECIMAL(12,2),
    premium_renewal DECIMAL(12,2),
    increase_percentage DECIMAL(5,2) GENERATED ALWAYS AS (
        CASE WHEN premium_current > 0
        THEN ((premium_renewal - premium_current) / premium_current) * 100
        ELSE 0 END
    ) STORED,
    risk_status renewal_risk_status DEFAULT 'SAFE',
    ai_strategy_notes TEXT,
    last_contact_date DATE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.renewal_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    renewal_id UUID REFERENCES public.project_85_renewals(id) ON DELETE CASCADE,
    action_type VARCHAR(100) NOT NULL,
    details JSONB,
    performed_by_role VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- -------------------------------------------------------------------------------------
-- 6. GUARDRAILS & REPORTING
-- -------------------------------------------------------------------------------------

CREATE TABLE public.guardrail_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_role VARCHAR(100) NOT NULL,
    attempted_action VARCHAR(255) NOT NULL,
    rule_violated TEXT NOT NULL,
    context_payload JSONB,
    severity VARCHAR(20) DEFAULT 'MEDIUM',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.reporting_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_name VARCHAR(100) NOT NULL,
    frequency report_frequency NOT NULL,
    target_slack_channel VARCHAR(50) REFERENCES public.slack_registry(channel_id),
    kpi_query_config JSONB,
    last_run TIMESTAMPTZ,
    next_run TIMESTAMPTZ
);

CREATE TABLE public.dashboard_kpis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_name VARCHAR(100) NOT NULL,
    metric_value DECIMAL(15,2) NOT NULL,
    category VARCHAR(50) NOT NULL,
    recorded_at TIMESTAMPTZ DEFAULT now()
);

-- -------------------------------------------------------------------------------------
-- 7. INDEXES
-- -------------------------------------------------------------------------------------
CREATE INDEX idx_commission_policy ON public.commission_audits(policy_number);
CREATE INDEX idx_commission_status ON public.commission_audits(status);
CREATE INDEX idx_project85_exp_date ON public.project_85_renewals(expiration_date);
CREATE INDEX idx_crm_queue_status ON public.crm_write_queue(status);
CREATE INDEX idx_guardrail_role ON public.guardrail_logs(agent_role);

-- -------------------------------------------------------------------------------------
-- 8. ROW LEVEL SECURITY (RLS)
-- -------------------------------------------------------------------------------------
ALTER TABLE public.slack_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hermes_ai_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_write_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.commission_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.eom_scorecards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.project_85_renewals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.renewal_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.guardrail_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reporting_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dashboard_kpis ENABLE ROW LEVEL SECURITY;

-- Hermes backend uses service_role key: grant via Postgres role (not JWT string match).
CREATE POLICY "Service Role Full Access" ON public.slack_registry
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.hermes_ai_roles
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.crm_write_queue
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.crm_receipts
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.commission_audits
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.eom_scorecards
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.project_85_renewals
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.renewal_actions
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.guardrail_logs
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.reporting_schedules
  FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service Role Full Access" ON public.dashboard_kpis
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "authenticated_select_slack_registry" ON public.slack_registry FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_hermes_ai_roles" ON public.hermes_ai_roles FOR SELECT TO authenticated USING (true);
CREATE POLICY "Users can view commissions" ON public.commission_audits FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_eom_scorecards" ON public.eom_scorecards FOR SELECT TO authenticated USING (true);
CREATE POLICY "Users can view renewals" ON public.project_85_renewals FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_renewal_actions" ON public.renewal_actions FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_guardrail_logs" ON public.guardrail_logs FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_reporting_schedules" ON public.reporting_schedules FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_select_dashboard_kpis" ON public.dashboard_kpis FOR SELECT TO authenticated USING (true);

