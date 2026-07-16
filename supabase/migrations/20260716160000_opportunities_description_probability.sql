-- Opportunity fields to match how RSG works the NowCerts pipeline:
--   description  — free-text notes beyond the single next_action line
--   probability  — win likelihood 0-100 (%); powers weighted pipeline value
ALTER TABLE public.opportunities ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE public.opportunities ADD COLUMN IF NOT EXISTS probability integer;

COMMENT ON COLUMN public.opportunities.probability IS 'Win likelihood 0-100 (percent). Weighted pipeline value = premium_estimate * probability/100.';
