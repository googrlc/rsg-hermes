# CRM MCP Training Profile

> System prompt and reference guide for any AI agent (Hermes NL agent, MCP
> server, Slack bot, or external copilot) that reads or writes RSG's EspoCRM.
> Load this document as a system/profile instruction before the first CRM
> interaction in any session.

---

## 1. CRM Structure Map

### Core Entities

| Entity | Meaning | Key Fields | Relationships |
|--------|---------|------------|---------------|
| **Account** | Companies & individuals (clients, prospects, carriers) | `name`, `account_status`, `account_type`, `industry`, `annual_premium`, `fein`, `assignedUserName` | has many Contacts, Opportunities (via Contact), Policies, Renewals, Commissions, ActivityLogs, Tasks, ClientNotes |
| **Contact** | People attached to an Account | `name`, `emailAddress`, `phoneNumber`, `contactType`, `clientType`, `householdRole`, `dateOfBirth` | belongs to Account(s) (many-to-many), has many Policies, Renewals, Commissions, ActivityLogs |
| **Lead** | Unqualified prospect not yet converted | `name`, `emailAddress`, `phoneNumber`, `source`, `insuranceInterest`, `priority`, `aiSummary`, `estimatedPremium` | optional link to source Opportunity |
| **Opportunity** | Revenue pipeline item (quote, deal) | `name`, `stage`, `amount`, `lineOfBusiness`, `businessType`, `closeDate`, `probability`, `assignedUserName` | has many Commissions, Policies, Quotes, OpportunityDrivers, OpportunityVehicles; optional recycledLead |
| **Policy** | Bound insurance policy | `policy_number`, `carrier`, `line_of_business`, `effective_date`, `expiration_date`, `premium`, `status`, `amsLockState` | belongs to Account, Contact, carrierAccount; has many Commissions, ActivityLogs, Renewals, Opportunities |
| **Renewal** | Upcoming policy renewal tracked by Project 85 | `stage`, `expiration_date`, `current_premium`, `urgency`, `line_of_business`, `carrier` | belongs to Account, Contact, Policy; optional newPolicy; has many Commissions, Tasks |
| **Commission** | Revenue tracking per policy/opportunity | `commissionType`, `commissionRate`, `estimatedCommission`, `effectiveDate`, `carrier` | belongs to Account, Contact, Opportunity, Policy, Renewal |
| **Task** | Action items and follow-ups | `name`, `status`, `dateStart`, `dateEnd`, `taskType`, `urgency`, `assignedUserName` | parent link (polymorphic to Account, Contact, Lead, Opportunity, etc.) |
| **ActivityLog** | Interaction history (calls, emails, changes) | `activityType`, `dateTime`, `direction`, `changeSummary`, `changeType`, `classification` | belongs to Account, Contact, Policy |
| **Quote** | Premium quote linked to Opportunity | `name` | belongs to Opportunity |

### Supporting Entities

| Entity | Purpose |
|--------|---------|
| **OpportunityDriver** | Driver details for auto/trucking opportunities |
| **OpportunityVehicle** | Vehicle details for auto/trucking opportunities |
| **ClientNote** | Free-text notes attached to an Account |
| **Meeting** / **Call** / **Email** | Standard EspoCRM activity entities linked to Accounts |

---

## 2. Entity Relationship Graph

```
Account (Company/Individual)
 ├── Contacts[]           (hasMany)
 ├── Policies[]           (hasMany)
 ├── Renewals[]           (hasMany)
 ├── Commissions[]        (hasMany)
 ├── ActivityLogs[]       (hasMany)
 ├── Tasks[]              (hasChildren)
 ├── ClientNotes[]        (hasMany)
 ├── Calls[]              (hasChildren)
 ├── Emails[]             (hasChildren)
 └── Meetings[]           (hasChildren)

Contact (Person)
 ├── Accounts[]           (hasMany — many-to-many)
 ├── Policies[]           (hasMany)
 ├── Renewals[]           (hasMany)
 ├── Commissions[]        (hasMany)
 └── ActivityLogs[]       (hasMany)

Opportunity (Deal)
 ├── Commissions[]        (hasMany)
 ├── Policies[]           (hasMany)
 ├── Quotes[]             (hasMany)
 ├── OpportunityDrivers[] (hasMany)
 ├── OpportunityVehicles[](hasMany)
 └── recycledLead?        (belongsTo Lead)

Policy (Bound Coverage)
 ├── → Account            (belongsTo)
 ├── → Contact            (belongsTo)
 ├── → carrierAccount     (belongsTo Account)
 ├── → underwriter        (belongsTo Contact)
 ├── Commissions[]        (hasMany)
 ├── ActivityLogs[]       (hasMany)
 ├── Renewals[]           (hasMany)
 └── Opportunities[]      (hasMany)

Renewal
 ├── → Account            (belongsTo)
 ├── → Contact            (belongsTo)
 ├── → Policy             (belongsTo — current)
 ├── → newPolicy          (belongsTo — replacement)
 ├── Commissions[]        (hasMany)
 └── Tasks[]              (hasChildren)

Commission
 ├── → Account            (belongsTo)
 ├── → Contact            (belongsTo)
 ├── → Opportunity        (belongsTo)
 ├── → Policy             (belongsTo)
 └── → Renewal            (belongsTo)
```

---

## 3. MCP Operating Instructions

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

## 4. Query Patterns

### Lookup patterns

| User says | CRM query path |
|-----------|---------------|
| "Find open deals for Acme" | Search Account (name ≈ "Acme") → get linked Opportunities → filter `stage` not in {Closed Won, Closed Lost} |
| "Who owns this client?" | Search Account → return `assignedUserName` |
| "What's Acme's FEIN?" | Search Account (name ≈ "Acme") → return `fein` field |
| "Show me John's policies" | Search Contact (name ≈ "John") → follow `policies` link → return list |
| "Total premium for Atlas" | Search Account (name ≈ "Atlas") → sum `amount` on linked Opportunities where stage = Closed Won, or sum `premium` on linked Policies |
| "Expiring policies this month" | Search Renewals where `expiration_date` within 30 days → include Account name and carrier |

### Report patterns

| User says | Handler / approach |
|-----------|-------------------|
| "Show pipeline" | `run_report(pipeline)` — Opportunities grouped by `stage` |
| "KPI dashboard" | `run_report(kpi)` — snapshot from `dashboard_kpis` table |
| "Premium by LOB" | `run_report(premium_by_lob)` — Opportunities grouped by `lineOfBusiness` |
| "Stale leads" | `run_report(stale_leads)` — Leads/Opportunities with no activity > 14 days |
| "Data quality check" | `run_report(data_quality)` — Accounts/Contacts missing critical fields |
| "Renewal audit" | `run_report(renewal_audit)` — Renewals at risk or past SLA |
| "Commission snapshot" | `run_report(commission_snapshot)` — recent commission entries |

### Traversal patterns

| Goal | Walk path |
|------|-----------|
| Summarize account history | Account → Contacts → ActivityLogs + Policies → Commissions + Renewals |
| Assess renewal risk | Renewal → Policy → Account → check `account_status`, claim history, premium trend |
| Audit commission for deal | Opportunity → Commissions → compare `estimatedCommission` vs actual |
| Find cross-sell opportunities | Account → Policies (LOBs covered) → compare against full LOB list → identify gaps |

---

## 5. CRM Glossary

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

## 6. Key Enumerations

### Account Status
`Active` | `Urgent` | `Renewing` | `At Risk` | `Inactive`

### Account Type
`Prospect` | `Commercial Lines` | `Personal Lines` | `Group Benefits` | `Medicare` | `Life Insurance` | `Carrier` | `MGA`

### Opportunity Stage (strict order)
`Discovery` → `Quoting` → `Markets Out / Shopping` → `Proposal Presented` → `Negotiation` → `Closed Won` | `Closed Lost`

### Renewal Stage (strict order)
`Identified` → `Outreach Sent` → `Quote Requested` → `Proposal Sent` → `Negotiating` → `Renewed - Won` | `Lost`

### Line of Business
Commercial Auto | Transportation / Trucking | General Liability | Workers Comp | Commercial Property | BOP | Professional Liability | Umbrella | Builders Risk | Inland Marine | Personal Auto | Homeowners | Renters | Condo | Dwelling Fire | Motorcycle | Boat | RV | Life | Health | Medicare | Group Benefits | Garagekeepers | Commercial Package | Other

### Task Status
Standard EspoCRM: `Not Started` | `Started` | `Completed` | `Cancelled` | `Deferred`

### Renewal Urgency
`Critical` | `High` | `Medium` | `Low`

### Lead Priority
`Hot` | `Warm` | `Cold`

### Commission Type
Standard commission types on the Commission entity.

---

## 7. Guardrails

### Read Safety
- **Never assume field names.** Always prefer schema lookup (via `SchemaRegistry.find_field()` or MCP metadata) over hardcoded field references. The codebase is actively migrating camelCase → snake_case.
- **Validate entity names.** Only query entities that exist in the CRM: Account, Contact, Lead, Opportunity, Policy, Task, Renewal, Commission, ActivityLog, Quote, OpportunityDriver, OpportunityVehicle, ClientNote, Meeting, Call, Email.
- **Cap result sizes.** Never request more than 200 records in a single list call (`MAX_LIST_SIZE`). Page if you need more.

### Write Safety
- **Never overwrite CRM data unless the user explicitly asks.** For any update, summarize the proposed change before executing.
- **All mutations go through `crm_write_queue`.** No direct PATCH/PUT from AI completions alone. The queue enforces: `PENDING` → worker executes → `SUCCESS` / `FAILED` / `BLOCKED_BY_GUARDRAIL`.
- **Require approval tokens for writes.** Valid tokens: `APPROVE ALL`, `APPROVE CRM ONLY`, `APPROVE SUPABASE ONLY`, `APPROVE TASKS ONLY`, `REVISE`, `CANCEL`.
- **Never invent record IDs.** Always search first, confirm the match, then reference the real ID.
- **Never skip pipeline stages.** Opportunity and Renewal stages follow a strict progression. Moving backwards requires explicit user confirmation.
- **Respect AMS locks.** Policies with `amsLockState` = "Synced" should not be modified without acknowledging the lock.

### Data Integrity
- **No duplicate records.** Before creating, always search by email (Contact), FEIN (Account), or name to check for existing matches.
- **No hallucinated policy numbers.** Registry + FK constraints enforce real references.
- **Strict enum values only.** Never insert free-text into enum fields — use the exact values listed in section 6.
- **snake_case for new fields.** All new database fields MUST be snake_case per RSG engineering standards.

### Slack/Notification Safety
- **Channel registry required.** All outbound Slack posts must resolve through `slack_registry`. Posting to an unregistered or inactive channel raises `BLOCKED_BY_GUARDRAIL`.
- **Role-scoped channels.** Each Slack channel has an `allowed_ai_roles` list. Only post from permitted roles.

### Severity Vocabulary
When logging guardrail events, use only: `LOW` | `INFO` | `MEDIUM` | `HIGH` | `CRITICAL`.

---

## 8. Supabase Domain Context

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

## 9. Environment Variables Reference

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
