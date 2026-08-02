-- =====================================================================================
-- NOWCERTS AGENT ID — who a task is assigned to, stated exactly rather than matched.
--
-- The AMS MCP's insert_task_tool takes `assigned_to` as a list of NowCerts agent UUIDs.
-- agency_crm_users had no such column: only email, display_name, role and active. So the
-- only way to assign a pushed task was to resolve a person by NAME at write time, via
-- get_agent_id_by_name_tool.
--
-- WHY NOT THAT. Name matching is the unreliable half of this. Two agents with similar
-- names, a display_name that does not match what NowCerts calls them, a middle initial,
-- a married name — each resolves to the wrong person or to nobody, silently, at the
-- moment of a write. This estate has already been bitten by exactly this class of
-- problem: CRM task creation failed on the agency_crm_users FK because some identities
-- were .net and some .com. Identity matching here is demonstrably fragile.
--
-- A misassigned task is worse than an unassigned one. Unassigned is visible — it sits in
-- a queue nobody owns and someone notices. Misassigned looks done: it is on a real
-- person's list, just not the person who was supposed to act, and nobody finds out until
-- the client does.
--
-- So the id is stored once, exactly, and read at write time. Nullable on purpose: a user
-- without an agent id simply produces an unassigned task (assigned_to is optional on
-- insert_task_tool — only `title` is required), which is the safe failure. Populate it
-- from get_agent_list_tool; do not infer it.
-- =====================================================================================

ALTER TABLE agency_crm_users
  ADD COLUMN IF NOT EXISTS nowcerts_agent_id uuid;

COMMENT ON COLUMN agency_crm_users.nowcerts_agent_id IS
  'NowCerts agent UUID for this user, used as insert_task_tool.assigned_to. '
  'Resolved once from get_agent_list_tool and stored — never matched by name at '
  'write time, because a misassigned task is invisible in a way an unassigned one '
  'is not. NULL means push the task unassigned.';

-- One CRM user per NowCerts agent. Two users pointing at the same agent would make
-- "who owns this" ambiguous in the direction that matters (reading back from the AMS).
CREATE UNIQUE INDEX IF NOT EXISTS agency_crm_users_nowcerts_agent_id_key
  ON agency_crm_users (nowcerts_agent_id)
  WHERE nowcerts_agent_id IS NOT NULL;
