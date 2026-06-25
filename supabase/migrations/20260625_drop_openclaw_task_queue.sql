-- Drop the deprecated OpenClaw task queue.
-- OpenClaw is no longer in use at RSG; Hermes is the only AI operations environment.
-- The producer endpoint, worker, and CLI flag have been removed from the repo.
-- Safe across all environments: IF EXISTS makes this a no-op where the table
-- was never created, and the table held zero rows at removal time.
DROP TABLE IF EXISTS public.openclaw_task_queue;
