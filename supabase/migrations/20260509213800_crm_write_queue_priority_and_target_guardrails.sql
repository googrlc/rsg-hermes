-- Enforce Hermes queue contract for EspoCRM writes.
ALTER TABLE public.crm_write_queue
  ADD COLUMN IF NOT EXISTS priority INT NOT NULL DEFAULT 1;

ALTER TABLE public.crm_write_queue
  DROP CONSTRAINT IF EXISTS crm_write_queue_priority_positive;
ALTER TABLE public.crm_write_queue
  ADD CONSTRAINT crm_write_queue_priority_positive
  CHECK (priority >= 1);

UPDATE public.crm_write_queue
SET target_system = 'EspoCRM'
WHERE target_system IS NULL OR target_system <> 'EspoCRM';

ALTER TABLE public.crm_write_queue
  ALTER COLUMN target_system SET DEFAULT 'EspoCRM';

ALTER TABLE public.crm_write_queue
  DROP CONSTRAINT IF EXISTS crm_write_queue_target_system_espocrm_only;
ALTER TABLE public.crm_write_queue
  ADD CONSTRAINT crm_write_queue_target_system_espocrm_only
  CHECK (target_system = 'EspoCRM');
