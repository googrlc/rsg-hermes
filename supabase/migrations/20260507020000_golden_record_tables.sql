-- Golden Record tables: unified client + commission view for bidirectional sync.
-- Supabase is the hub between EspoCRM (CRM) and NowCerts (AMS).

-- ── crm_accounts: unified client record ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS crm_accounts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    espocrm_id TEXT UNIQUE,
    nowcerts_id TEXT UNIQUE,
    name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    account_type TEXT,
    fein TEXT,
    address_street TEXT,
    address_city TEXT,
    address_state TEXT,
    address_zip TEXT,
    email TEXT,
    phone TEXT,
    website TEXT,
    business_entity TEXT,
    year_business_started INT,
    source_system TEXT NOT NULL DEFAULT 'espocrm',
    last_espo_sync_at TIMESTAMPTZ,
    last_nowcerts_sync_at TIMESTAMPTZ,
    raw_espo_payload JSONB,
    raw_nowcerts_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_accounts_espocrm_id ON crm_accounts(espocrm_id);
CREATE INDEX IF NOT EXISTS idx_crm_accounts_nowcerts_id ON crm_accounts(nowcerts_id);
CREATE INDEX IF NOT EXISTS idx_crm_accounts_source ON crm_accounts(source_system);
CREATE INDEX IF NOT EXISTS idx_crm_accounts_fein ON crm_accounts(fein);

-- ── crm_commissions: commission tracking per policy ──────────────────────────
CREATE TABLE IF NOT EXISTS crm_commissions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    account_id UUID REFERENCES crm_accounts(id),
    policy_number TEXT,
    carrier TEXT,
    line_of_business TEXT,
    premium NUMERIC(12,2),
    commission_rate NUMERIC(5,2),
    commission_amount NUMERIC(12,2),
    agency_fee NUMERIC(12,2),
    effective_date DATE,
    expiration_date DATE,
    policy_status TEXT,
    source_system TEXT NOT NULL,
    espocrm_id TEXT,
    nowcerts_id TEXT,
    last_synced_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crm_commissions_account ON crm_commissions(account_id);
CREATE INDEX IF NOT EXISTS idx_crm_commissions_policy ON crm_commissions(policy_number);
CREATE INDEX IF NOT EXISTS idx_crm_commissions_source ON crm_commissions(source_system);

-- ── sync_log: bidirectional sync run log ─────────────────────────────────────
-- Extends the existing sync_runs table pattern for reverse (CRM→AMS) direction.
-- No new table needed — we reuse sync_runs with different workflow_name values:
--   'crm_to_hub'       — EspoCRM → Supabase mirror
--   'hub_to_nowcerts'   — Supabase → NowCerts push
--   'bidirectional'     — Full round-trip

-- ── RLS (match existing pattern from sync_control_tables migration) ──────────
ALTER TABLE crm_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_commissions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_crm_accounts" ON crm_accounts
    FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "service_role_crm_commissions" ON crm_commissions
    FOR ALL USING (auth.role() = 'service_role');
