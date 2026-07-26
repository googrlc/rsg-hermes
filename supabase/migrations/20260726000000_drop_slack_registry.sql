-- Drop public.slack_registry (Slack decommission, 2026-07-26).
--
-- NOT YET APPLIED. Run this deliberately — it is destructive.
--
-- The table gated report posting: `guardrails.validate_slack_channel` refused
-- any channel that was not registered, active, and role-allowed. Both that
-- function and its only caller (`operations/slack_router.py`, renamed to
-- report_router.py) were deleted in the same change as this file, because
-- nothing imported the router — the guardrail had been dead code, and the
-- registry was read by nothing but itself and one `--ops-doctor` check.
--
-- Delivery now goes to Nextcloud Talk via `integrations/team_notify`, which
-- routes on category names (boss / renewals / systems) resolved to room tokens
-- from HERMES_TALK_ROOM_*. There are no Slack channel ids left to validate.
--
-- `--ops-doctor` no longer probes this table, so dropping it will not make the
-- readiness check fail. Verify that first if you are on an older image:
--   select 1 from information_schema.tables
--    where table_schema='public' and table_name='slack_registry';
--
-- Kept deliberately, do NOT drop with this:
--   * guardrail_logs — rows reference rule_violated='slack_registry_miss' as
--     historical evidence; the log table itself is still written by
--     renewals/executor.py.
--   * renewal_actions.action_type = 'SLACK_ALERT' — a stored enum value on
--     existing rows. Renaming it needs its own backfill.

begin;

drop table if exists public.slack_registry;

commit;

-- Verify afterwards — should return zero rows:
--   select table_name from information_schema.tables
--    where table_schema='public' and table_name = 'slack_registry';
