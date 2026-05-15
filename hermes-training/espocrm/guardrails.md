# EspoCRM Guardrails

> Safety rules for any AI agent interacting with RSG's EspoCRM. These rules
> are non-negotiable — violations are logged to `guardrail_logs` in Supabase.

---

## Read Safety

- **Never assume field names.** Always prefer schema lookup (via
  `SchemaRegistry.find_field()` or MCP metadata) over hardcoded field
  references. The codebase is actively migrating camelCase → snake_case.

- **Validate entity names.** Only query entities that exist in the CRM:
  Account, Contact, Lead, Opportunity, Policy, Task, Renewal, Commission,
  ActivityLog, Quote, OpportunityDriver, OpportunityVehicle, ClientNote,
  Meeting, Call, Email.

- **Cap result sizes.** Never request more than 200 records in a single list
  call (`MAX_LIST_SIZE`). Page if you need more.

- **Walk relationships, don't guess.** A contact may belong to multiple
  accounts. Always verify the link before assuming ownership.

---

## Write Safety

- **Never overwrite CRM data unless the user explicitly asks.** For any
  update, summarize the proposed change before executing.

- **All mutations go through `crm_write_queue`.** No direct PATCH/PUT from
  AI completions alone. The queue enforces:
  `PENDING` → worker executes → `SUCCESS` / `FAILED` / `BLOCKED_BY_GUARDRAIL`.

- **Require approval tokens for writes.** Valid tokens:
  - `APPROVE ALL` — execute all staged changes
  - `APPROVE CRM ONLY` — execute CRM changes only
  - `APPROVE SUPABASE ONLY` — execute Supabase changes only
  - `APPROVE TASKS ONLY` — execute Task changes only
  - `REVISE` — re-draft the proposed change
  - `CANCEL` — discard the proposed change

- **Never invent record IDs.** Always search first, confirm the match, then
  reference the real ID.

- **Never skip pipeline stages.** Opportunity and Renewal stages follow a
  strict progression. Moving backwards requires explicit user confirmation.

- **Respect AMS locks.** Policies with `amsLockState` = "Synced" should not
  be modified without acknowledging the lock and documenting the reason.

---

## Data Integrity

- **No duplicate records.** Before creating, always search by:
  - Email (Contact)
  - FEIN (Account — business)
  - Name (fallback for all entities)

- **No hallucinated policy numbers.** Registry + FK constraints enforce real
  references. Never fabricate a policy number.

- **Strict enum values only.** Never insert free-text into enum fields. Use
  the exact values from the schema. Key enums:
  - Account Status: `Active`, `Urgent`, `Renewing`, `At Risk`, `Inactive`
  - Opportunity Stage: `Discovery` → `Quoting` → `Markets Out / Shopping` →
    `Proposal Presented` → `Negotiation` → `Closed Won` | `Closed Lost`
  - Renewal Stage: `Identified` → `Outreach Sent` → `Quote Requested` →
    `Proposal Sent` → `Negotiating` → `Renewed - Won` | `Lost`

- **snake_case for new fields.** All new database fields MUST be snake_case
  per RSG engineering standards. Reference
  `custom-fields-camelcase-audit.csv` before modifying entities.

---

## Slack / Notification Safety

- **Channel registry required.** All outbound Slack posts must resolve
  through `slack_registry`. Posting to an unregistered or inactive channel
  raises `BLOCKED_BY_GUARDRAIL`.

- **Role-scoped channels.** Each Slack channel has an `allowed_ai_roles`
  list. Only post from permitted roles:

  | Channel | Allowed Roles |
  |---------|---------------|
  | `#rsg-hermes-commission-audit` | HermesCommissionAuditor, HermesFinanceOps |
  | `#rsg-hermes-project85-renewals` | HermesRenewalSpecialist, HermesOpsRouter |
  | `#rsg-hermes-operations` | HermesOpsRouter, HermesFinanceOps |

---

## Severity Vocabulary

When logging guardrail events, use only these values:

`LOW` | `INFO` | `MEDIUM` | `HIGH` | `CRITICAL`

---

## Prevention Rules Summary

| Rule | Enforcement |
|------|-------------|
| No channel drift | `slack_registry` lookup required; miss = BLOCKED_BY_GUARDRAIL |
| No direct CRM writes | All mutations go through `crm_write_queue` |
| No hallucinated policy numbers | Registry + FK + enum constraints |
| No invented states | Strict enums: `sync_status`, `commission_status`, `renewal_risk_status` |
| No duplicate commission rows | Unique index on `(statement_id, policy_number, snapshot_month)` |
| Nonnegative `attempt_count` | CHECK constraint on `crm_write_queue` |
| EOM scorecard lock | `is_locked` on `eom_scorecards` gates mutations after month close |

---

## Database Safeguards

- Nonnegative `attempt_count` on `crm_write_queue`
- `ON DELETE SET NULL` for Slack FK on `reporting_schedules`
- `hermes_touch_updated_at()` trigger on mutation-heavy tables
- Partial index `idx_crm_queue_open_work` for worker scanning
