# EspoCRM Workflows & Operating Context

---

## MCP Operating Instructions

When using the CRM MCP or any Hermes interface:

1. **Identify the relevant CRM object first.** Map the user's language to
   Account, Contact, Lead, Opportunity, Policy, Renewal, Commission, Task,
   or ActivityLog before querying.

2. **Always check relationships before answering.** A contact may belong to
   multiple accounts. A deal (Opportunity) links to Policies and Commissions.
   Walk the graph rather than guessing.

3. **Inspect schema/metadata when uncertain.** Use `SchemaRegistry` or the
   MCP `get_crm_record` tool to verify field names before constructing
   queries. Field names are actively migrating from camelCase to snake_case
   — never assume the casing.

4. **Respect the stage pipeline.** Opportunities follow a strict progression:
   `Discovery` → `Quoting` → `Markets Out / Shopping` → `Proposal Presented`
   → `Negotiation` → `Closed Won` | `Closed Lost`. Never skip stages or
   invent new ones.

5. **Renewal stages (Project 85):** `Identified` → `Outreach Sent` →
   `Quote Requested` → `Proposal Sent` → `Negotiating` → `Renewed - Won` |
   `Lost`.

6. **Use `lineOfBusiness` for filtering.** It appears on Opportunity, Policy,
   and Renewal with a shared vocabulary: Commercial Auto, General Liability,
   Workers Comp, Homeowners, Medicare, Life, Group Benefits, etc.

---

## CRM Glossary

| Term | Definition | CRM Mapping |
|------|-----------|-------------|
| **Client** | Active insured entity | Account where `account_status` = "Active" |
| **Prospect** | Potential client, not yet bound | Account where `account_status` = "Urgent" or `account_type` = "Prospect"; or an unconverted Lead |
| **Pipeline** | Active revenue opportunities | Opportunities where `stage` not in {Closed Won, Closed Lost} |
| **Renewal** | Upcoming policy expiration requiring action | Renewal entity; 90/60/30-day checkpoints drive outreach |
| **X-date** | Expiration date of a policy | `expiration_date` on Policy or Renewal |
| **LOB** | Line of Business | `lineOfBusiness` on Opportunity / `line_of_business` on Policy, Renewal |
| **Carrier** | Insurance company providing coverage | `carrier` field on Policy, Renewal, Commission; or Account with `account_type` = "Carrier" |
| **MGA** | Managing General Agent | Account with `account_type` = "MGA" |
| **FEIN** | Federal Employer ID Number | `fein` field on Account |
| **DOT Number** | Department of Transportation number (trucking) | `caDotNumber` on Opportunity |
| **MC Number** | Motor Carrier number | `caMcNumber` on Opportunity |
| **Project 85** | RSG's retention initiative targeting 85% retention | Renewals tracked via the `project_85_renewals` Supabase table and EspoCRM Renewal entity |
| **Golden Record** | Supabase-hosted source of truth for synced data | `leads_staging`, `crm_write_queue`, etc. in Supabase |
| **Write Gate** | Safety mechanism requiring approval before CRM mutations | `crm_write_queue` with approval tokens (APPROVE ALL, APPROVE CRM ONLY, etc.) |
| **Intel Fields** | AI-populated research fields on Account | `ai_assessment`, `bbb_rating`, `intel_confidence`, `intel_entity_type`, etc. |
| **AMS Lock** | Policy synced from NowCerts AMS, protected from manual edits | `amsLockState` on Policy (enum: Pending Sync, Synced) |
| **Momentum** | NowCerts task/activity sync system | `momentumLastSynced`, `momentumTaskId` on Task |

---

## CRM Write Queue Workflow

All CRM mutations flow through the write queue — no direct PATCH/PUT from AI.

```
User Request
  → AI parses intent + fields
  → Payload inserted into crm_write_queue (status: PENDING)
  → User reviews and sends approval token
      APPROVE ALL | APPROVE CRM ONLY | APPROVE SUPABASE ONLY | APPROVE TASKS ONLY | REVISE | CANCEL
  → Worker dequeues and executes POST/PUT to EspoCRM
  → crm_receipts logged with transaction_id + raw_response
  → Status updated: SUCCESS | FAILED | BLOCKED_BY_GUARDRAIL
```

---

## Supabase Domain Context

Hermes uses Supabase as a governance and staging layer alongside EspoCRM:

| Domain | Key Tables |
|--------|-----------|
| CRM Governance | `crm_write_queue`, `crm_receipts`, `guardrail_logs` |
| Intake / Documents | `leads_staging`, `documents`, `review_queue`, `stg_slack_intake_notes` |
| Underwriting | `risk_assessments`, `uw_submission_profile`, `carrier_appetite` |
| Commission | `commission_audits`, `eom_scorecards`, `commission_ledger` |
| Project 85 | `project_85_renewals`, `renewal_actions` |
| Operations | `dashboard_kpis`, `reporting_schedules`, `slack_registry`, `hermes_ai_roles` |

---

## Hermes AI Roles

| Role | Scope | CRM Access | Success Criteria |
|------|-------|------------|------------------|
| **HermesCommissionAuditor** | `commission_audits`, `eom_scorecards`, `crm_write_queue` | Queue only | Variance explained or zero; EOM rollups balanced |
| **HermesRenewalSpecialist** | `project_85_renewals`, `renewal_actions`, `slack_registry` | Queue only | Action log complete; risk status from enum; escalations to Slack |
| **HermesFinanceOps** | `commission_audits`, `eom_scorecards`, `dashboard_kpis` | None | Scorecard numbers traceable to audits; month lock respected |
| **HermesOpsRouter** | `slack_registry`, `reporting_schedules`, `guardrail_logs` | Queue only | Posts only via registry channels; guardrail on unknown Slack targets |

---

## Reporting Schedule

| Frequency | Report | Slack Target |
|-----------|--------|-------------|
| Daily | Hermes Daily Ops Pulse | `#rsg-hermes-operations` |
| Weekly | Commission Discrepancy Rollup | `#rsg-hermes-commission-audit` |
| Monthly | Renewal SLA Premium Delta | `#rsg-hermes-project85-renewals` |

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ESPO_URL` | EspoCRM REST API base URL |
| `ESPO_API_KEY` | EspoCRM X-Api-Key authentication |
| `SUPABASE_URL` | Supabase PostgREST base URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side CRUD via RLS |
| `SLACK_BOT_TOKEN` | Slack message posting |
| `SLACK_APP_TOKEN` | Slack Socket Mode |
| `OPENAI_API_KEY` | NLP intent fallback |
| `HERMES_VERIFY_TLS` | Set `"true"` to enable TLS verification |
| `HERMES_MAX_LIST_SIZE` | Max records per list call (default: 200) |
| `HERMES_ACCOUNT_FEIN_ATTR` | Custom FEIN attribute name (default: `fein`) |

> **No secrets in this file.** All credentials are referenced by environment
> variable name only. See your `.env` file or secrets manager for actual values.
