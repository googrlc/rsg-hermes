# EspoCRM Query Patterns

> Examples mapping natural language requests to CRM query paths. Use these as
> training data for intent routing and as reference for building new handlers.

---

## Lookup Patterns

| User says | CRM query path |
|-----------|---------------|
| "Find open deals for Acme" | Search Account (name ~ "Acme") → get linked Opportunities → filter `stage` not in {Closed Won, Closed Lost} |
| "Who owns this client?" | Search Account → return `assignedUserName` |
| "What's Acme's FEIN?" | Search Account (name ~ "Acme") → return `fein` field |
| "Show me John's policies" | Search Contact (name ~ "John") → follow `policies` link → return list |
| "Total premium for Atlas" | Search Account (name ~ "Atlas") → sum `amount` on linked Opportunities where stage = Closed Won, or sum `premium` on linked Policies |
| "Expiring policies this month" | Search Renewals where `expiration_date` within 30 days → include Account name and carrier |
| "What's the DOT number for ABC Trucking?" | Search Account/Opportunity (name ~ "ABC Trucking") → return `caDotNumber` |
| "Show all Medicare contacts" | Search Contacts where linked Account has `account_type` = "Medicare" |
| "Who is our contact at Acme?" | Search Account (name ~ "Acme") → follow `contacts` link → return list |
| "Find accounts with no recent activity" | Search Accounts → filter where `lastContactDate` > 30 days ago |

---

## Report Patterns

| User says | Handler / approach |
|-----------|-------------------|
| "Show pipeline" | `run_report(pipeline)` — Opportunities grouped by `stage` |
| "KPI dashboard" | `run_report(kpi)` — snapshot from `dashboard_kpis` table |
| "Premium by LOB" | `run_report(premium_by_lob)` — Opportunities grouped by `lineOfBusiness` |
| "Stale leads" | `run_report(stale_leads)` — Leads/Opportunities with no activity > 14 days |
| "Data quality check" | `run_report(data_quality)` — Accounts/Contacts missing critical fields |
| "Renewal audit" | `run_report(renewal_audit)` — Renewals at risk or past SLA |
| "Commission snapshot" | `run_report(commission_snapshot)` — recent commission entries |
| "My accounts" | `run_report(my_accounts)` — Accounts assigned to current user |
| "Cross-sell report" | `run_report(cross_sell)` — Accounts with coverage gaps |

---

## Traversal Patterns

| Goal | Walk path |
|------|-----------|
| Summarize account history | Account → Contacts → ActivityLogs + Policies → Commissions + Renewals |
| Assess renewal risk | Renewal → Policy → Account → check `account_status`, claim history, premium trend |
| Audit commission for deal | Opportunity → Commissions → compare `estimatedCommission` vs actual |
| Find cross-sell opportunities | Account → Policies (LOBs covered) → compare against full LOB list → identify gaps |
| Full contact exposure | Contact → Accounts[] → Policies[] → sum premiums per LOB |
| Carrier book analysis | Account (type=Carrier) → carrierPolicies → count + sum premiums by LOB |

---

## Write Patterns

All writes go through `crm_write_queue`. These patterns show the intent-to-queue mapping:

| User says | Queue payload shape |
|-----------|-------------------|
| "Create account Acme Corp" | `entity: Account, action: create, fields: {name: "Acme Corp"}` |
| "Add contact John Smith to Acme" | Search Account "Acme" for ID → `entity: Contact, action: create, fields: {firstName: "John", lastName: "Smith", accountId: "<id>"}` |
| "Move opportunity to Quoting" | Search Opportunity → `entity: Opportunity, action: update, record_id: "<id>", fields: {stage: "Quoting"}` |
| "Log a call with Acme" | `entity: ActivityLog, action: create, fields: {activityType: "Call", accountId: "<id>", direction: "Outbound"}` |
| "Update FEIN for Atlas" | Search Account "Atlas" → `entity: Account, action: update, record_id: "<id>", fields: {fein: "<value>"}` |

---

## Intake Patterns

Casual lead intake messages are parsed by the NL agent:

| User says | Parsed output |
|-----------|--------------|
| "Just met Juan Silva at Peterbilt, needs fleet quote for 3 trucks" | Lead: Juan Silva, Account: Peterbilt, LOB: Commercial Auto, vehicleCount: 3 |
| "Got a call from Jane at 555-1234, wants home and auto" | Lead: Jane, phone: 555-1234, LOBs: [Homeowners, Personal Auto] |
| "Referral from Bob — ABC Construction needs GL and WC" | Lead: ABC Construction, source: Referral, LOBs: [General Liability, Workers Comp] |

---

## MCP Tool Mapping

| MCP Tool | Purpose | Example |
|----------|---------|---------|
| `search_contacts` | Search contacts by name/email/phone | `search_contacts(query: "John Smith")` |
| `search_accounts` | Search accounts by name | `search_accounts(query: "Acme")` |
| `get_crm_record` | Get single record by entity + ID | `get_crm_record(entity: "Account", id: "abc123")` |
| `list_open_tasks` | List non-completed tasks | `list_open_tasks(query: "renewal")` |

### Hermes NL Agent Tools

| Tool | Purpose |
|------|---------|
| `search_records` | Search any entity by name |
| `get_field_value` | Get a specific field for a record |
| `run_report` | Generate a pipeline/KPI/audit report |
| `total_premium` | Calculate total premium for an account |
| `create_record` | Create a new CRM record (queued) |
| `update_record` | Update an existing record (queued) |
| `intake_lead` | Parse unstructured lead intake text |
