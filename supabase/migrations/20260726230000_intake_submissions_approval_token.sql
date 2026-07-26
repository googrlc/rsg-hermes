-- Persist the approval token on intake_submissions so the intake worker can branch
-- on it. Until now the token lived only inside the latest status_history note
-- ("APPROVE CRM ONLY by U-LAMAR"), which the worker never read — so every
-- partial-scope token (CRM ONLY / SUPABASE ONLY / TASKS ONLY) behaved identically
-- to APPROVE ALL. A dedicated column makes the worker's scope control reliable
-- instead of parsing a free-text note.
--
-- Idempotent: safe to re-run. Existing rows keep NULL; the worker treats a NULL
-- token as APPROVE ALL (its historical behaviour), so in-flight submissions are
-- unaffected.
ALTER TABLE public.intake_submissions
    ADD COLUMN IF NOT EXISTS approval_token TEXT;

COMMENT ON COLUMN public.intake_submissions.approval_token IS
    'The APPROVE * token the approver chose (APPROVE ALL / CRM ONLY / SUPABASE '
    'ONLY / TASKS ONLY). NULL on old rows; the intake worker defaults NULL to '
    'APPROVE ALL.';
