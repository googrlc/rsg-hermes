-- =====================================================================================
-- Zoho CRM cross-reference on opportunities.
-- When HERMES_WRITE_TO_ZOHO is on, commit_intake mirrors the intake into Zoho and
-- stamps the resulting Account / Deal ids onto every pipeline row opened by that
-- intake so Hermes can reconcile later without re-searching Zoho.
-- =====================================================================================

ALTER TABLE public.opportunities
    ADD COLUMN IF NOT EXISTS zoho_account_id TEXT,
    ADD COLUMN IF NOT EXISTS zoho_deal_ids   JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_opportunities_zoho_account_id
    ON public.opportunities (zoho_account_id)
    WHERE zoho_account_id IS NOT NULL;

COMMENT ON COLUMN public.opportunities.zoho_account_id IS
    'Zoho CRM Accounts.id stamped by hermes.intake.commit when HERMES_WRITE_TO_ZOHO is on.';
COMMENT ON COLUMN public.opportunities.zoho_deal_ids IS
    'Zoho CRM Deals.id list created for this intake (JSONB array of text ids).';
