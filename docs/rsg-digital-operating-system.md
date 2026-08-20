# Risk Solutions Group (RSG) Digital Operating System

## AI Usage Scope

This document is intended to help Amy and related AI systems understand the operating model for Risk Solutions Group. It should be used as foundational context for agency architecture, system boundaries, tool routing, repository purpose, and future workflow design. It is not intended to replace official policies, client records, carrier documents, or system-of-record data.

## Mission

Build a single AI-powered operating system for Risk Solutions Group that allows staff to interact with agency knowledge, carrier intelligence, client information, renewals, commissions, and procedures through one assistant: **Amy**.

Amy is not another database.

Amy is not another CRM.

Amy is not another AMS.

Amy is the **intelligence and orchestration layer** sitting on top of existing systems.

---

## Core Design Principle

### Systems of Record Stay Put

We do **not** replace existing systems. Every system continues doing what it does best.

```text
Momentum / NowCerts = Policy System of Record
Zoho CRM            = Sales / relationship System of Record
Zoho Desk           = Service case / workflow layer
SharePoint          = Document & Knowledge System
Supabase            = Intelligence, Search, Analytics
Microsoft Copilot   = User Experience Layer
```

> **Migration note.** The custom Command Center CRM (Supabase-backed) is being
> **decommissioned** in favor of **Zoho** as the CRM system of record. During the
> cutover, CRM relationship data moves Command Center → Zoho, and Command Center
> CRM reads become read-only until they are repointed at Zoho. NowCerts/Momentum
> (policy truth) and SharePoint (knowledge) are unaffected.

## Glossary

- **Amy:** The agency assistant and primary AI interface for Risk Solutions Group.
- **System of record:** The official source for a specific data type.
- **Intelligence layer:** The search, analytics, enrichment, and reasoning layer that helps Amy retrieve and interpret information.
- **Tool:** A behind-the-scenes capability Amy can call to retrieve information or trigger an approved workflow.
- **Write action:** Any action that creates, edits, sends, updates, or triggers something outside the AI conversation.

---

## Source-of-Truth Hierarchy

When systems disagree, Amy should defer to the system of record for that data type. Momentum or NowCerts controls policy and coverage data. Zoho CRM controls CRM relationship and pipeline data. Zoho Desk controls service-case workflow (status, owner, SLA) once it is live. SharePoint controls procedures, templates, training, and internal knowledge. Supabase supports search, classification, analytics, and intelligence, but should not override the official source systems.

### Synchronization & Freshness

Because Supabase mirrors and enriches data owned elsewhere, every mirrored dataset needs a defined refresh path and a freshness expectation — not just a rule for who wins a disagreement. Today the canonical book and intelligence layer are populated by scheduled Hermes jobs:

- `--sync-nowcerts` / `--sync-canonical-book` — pull AMS insureds and policies into the canonical book.
- `--sync-hub-to-nowcerts` — push approved changes back to the AMS.
- (post-migration) an equivalent **Zoho ↔ Supabase** sync for CRM relationship data.

Rules of thumb:

- Amy prefers the system of record for anything time-sensitive (binding status, current coverage, today's renewal list) and treats Supabase as a fast index/cache.
- Each mirrored answer should carry a *last-synced* signal so Amy can flag stale data ("this reflects the nightly mirror as of &lt;time&gt;") instead of presenting it as live.
- Write-backs are one-directional and approval-gated; Supabase never silently overwrites a system of record.

---

## Target User Experience

Instead of logging into multiple systems:

```text
Zoho
SharePoint
Momentum
Supabase
Outlook
Teams
```

Users simply ask Amy:

```text
What carriers write this risk?
Show me renewals due this month.
What policies does ABC Company have?
What licensing requirements apply?
What commissions need review?
How do we process a commercial auto submission?
```

Amy routes to the correct source automatically.

---

## Architecture

```text
                         Amy
                  (Copilot Studio)              ← user experience layer
                          |
                rsg-hermes MCP bridge           ← "one door" tool surface
                          |
             hermes-api (tools + domain logic)  ← intelligence / backend
                          |
       ┌──────────┬──────────┬──────────┬──────────┐
       |          |          |          |          |
    Supabase   Zoho CRM   Zoho Desk   Momentum AMS
  Intelligence Sales(SoR) Cases       Policy (SoR)
       |          |          |          |
       └──────────┴──────────┴──────────┴──────────┘
                          |
              SharePoint            Nextcloud
              Knowledge             Documents

  Power Automate — scheduled system-to-system glue between the stores above
```

**Reading the diagram.** Copilot Studio is the *replacement* for the decommissioned Command Center web UI, not a parallel build. Amy does not talk to the source systems directly: requests flow through the `rsg-hermes` MCP bridge (the single public "one door") to `hermes-api`, which hosts the tools and domain logic and reads/writes the stores below it. SharePoint knowledge is surfaced through Copilot's native grounding; Power Automate handles scheduled system-to-system syncs rather than live user requests.

## Amy Operating Rules

Amy should answer from known sources, identify the system used when helpful, and avoid guessing when information is missing. Amy should ask for clarification when a client, risk, carrier, policy, or workflow cannot be identified with confidence. Amy should treat write actions, task creation, client updates, workflow execution, and external communications as higher-risk actions that require clear authorization and logging.

---

## Current Repositories

### rsg-hermes

Primary platform repository.

**Purpose:** Agency Operating System

Acts as:

- Central architecture
- AI platform
- Integration hub
- Headless tools / intelligence backend behind Amy (exposed via the `rsg-hermes` MCP bridge)

After the CRM and user experience migrate to Zoho and Copilot Studio, Hermes keeps this backend role. It stops being the CRM and the UI — it does not stop being the platform. The heavy domain logic Amy relies on (carrier-appetite matching, class-code lookup, the Project 85 renewal engine, revenue sentinel, commission reconciliation, intake extraction, deliverable/PDF generation, canonical-book sync, KPI snapshots) continues to live here and is called as tools.

### rsg-hermes-infra

**Purpose:** Infrastructure

Contains:

- Deployment configuration
- Environment setup
- Hosting
- Security
- Infrastructure documentation

### rsg-obsidian-vault

**Purpose:** Agency Brain

Contains:

- Procedures
- Underwriting knowledge
- Carrier information
- Strategic planning
- Operations

This becomes the foundation for SharePoint knowledge.

### rsg-carrierhub

**Purpose:** Carrier Intelligence

May eventually become a module inside Hermes.

Contains:

- Carrier appetite
- Contacts
- Guidelines
- Carrier relationships

### rsg-commission-tracker

**Purpose:** Commission Intelligence

May eventually become a Hermes module.

Contains:

- Commission analysis
- Reconciliation
- Tracking

### rsg-cptintake

**Purpose:** Submission / Intake Automation

Likely becomes an intake module.

### rsg-agency-portal

**Purpose:** User Interface

Potential future employee and client portal.

## Repository Rule

Hermes should remain the primary platform repository unless a new repository has a clearly different deployment boundary, security model, or runtime. CarrierHub, Commission Tracker, CPT Intake, and Agency Portal should be treated as modules or applications within the broader Hermes operating system unless there is a strong technical reason to separate them.

> The existing repositories predate this principle. "Avoid creating more repositories" (below) means *stop adding new ones* and fold CarrierHub, Commission Tracker, CPT Intake, and Agency Portal into Hermes over time — not that the current count is a contradiction.

---

## Supabase

### Purpose

Supabase is not the CRM. Supabase is not the AMS. Supabase is the agency intelligence layer used for search, classification, analytics, enrichment, and AI-accessible operational context. It may reference CRM or AMS data, but it should not become the official record for clients, policies, coverage, or carrier documents.

### Current Capabilities

#### Classification

Supports:

- GL Codes
- WC Codes
- NAICS
- SIC
- Operations Mapping

#### Carrier Intelligence

Supports:

- Carrier appetite
- Carrier contacts
- Appetite search
- Risk matching

#### Commission Intelligence

Supports:

- Reconciliation
- Escalations
- Ledger review

#### CRM Intelligence

Supports:

- Client profile lookup
- Open case retrieval

> Sourced from **Zoho** (the CRM system of record) — or a Zoho → Supabase mirror —
> not the decommissioned Command Center CRM. Supabase enriches and indexes this
> data for search; it does not own it.

#### Semantic Search

Supports:

- AI knowledge retrieval
- Carrier retrieval
- Agency procedure retrieval

---

## Amy

> **Getting started:** operational wiring (smoke tests, Copilot Studio, rollout phases) is in
> [`amy-getting-started.md`](amy-getting-started.md).

### Design Philosophy

Amy is **one assistant**.

Avoid creating separate visible assistants such as:

```text
Carrier Agent
Commission Agent
Renewal Agent
Licensing Agent
```

Instead, use **Amy** with specialized tools behind the scenes.

---

## Amy Tool Map

Each Amy tool should have a clear purpose, expected inputs, source system, output type, and permission level. The tool map should help Amy decide which capability to use for a user request and when to ask for clarification before running a tool. Tools that retrieve information should be separated from tools that write, update, create tasks, or trigger workflows.

### Live runtime tools

The table below is generated from the live catalog at `GET /api/command-center/skills`
(`hermes-api`). These are the callable tools Amy invokes; they are almost entirely
**read** today, with writes handled through separate, approval-gated paths (see the
note under the table). Regenerate this table whenever the catalog changes.

| Tool | Purpose | Source system | Tier |
|---|---|---|---|
| `find_client` | Search the canonical client book by name | Canonical book (NowCerts mirror) | Read |
| `client_policies` | A client's policies with active count | Canonical book (NowCerts mirror) | Read |
| `ams_client_snapshot` | Live insured/policy snapshot for one client | NowCerts (live AMS) | Read |
| `crm_client_activity` | Open cases + tasks for a client | Command Center CRM (→ Zoho) | Read |
| `run_report` | Run a CRM report or dashboard view | Command Center CRM (→ Zoho) / Supabase | Read |
| `list_cases` | Open service cases with checklist progress | Command Center CRM (→ Zoho) | Read |
| `case_progress` | One case in full with its task checklist | Command Center CRM (→ Zoho) | Read |
| `renewals_overview` | Upcoming/at-risk renewals (Project 85) | Supabase (`project_85_renewals`) | Read |
| `list_carriers` | Carriers RSG has appointments/data on | Supabase (`carriers`) | Read |
| `match_carrier_appetite` | Carriers whose appetite matches a risk | Supabase (`carrier_appetite`) | Read |
| `appointments_by_line` | Carrier panel grouped by line of business | Supabase (carriers / appointments) | Read |
| `lookup_class_code` | What a GL/WC class code means | Supabase (classification tables) | Read |
| `commission_summary` | Expected vs received commission totals | Supabase (commission ledger) | Read |
| `commission_shortfalls` | Policies underpaid or missing a statement | Supabase (commission ledger) | Read |
| `client_documents` | A client's documents | Nextcloud | Read |
| `email_search` | Search the agency mailbox | Microsoft 365 | Read |
| `web_research` | Research a business/client on the public web | Public web | Read |
| `list_intake_submissions` | Recent intake submissions and status | Supabase (intake queue) | Read |
| `list_skills` | List Hermes's own tools + domain playbooks | Hermes (self) | Read (meta) |
| `intake_lead` | Process a casual lead intake message | Intake queue → CRM | **Write** (staged; requires approval) |

**Writes are deliberately not in the read tool set.** Higher-risk actions — moving a
pipeline opportunity, sending a record to NowCerts, executing a renewal, committing
commission/money data — run through separate approval-gated endpoints and skills
(for example `hermes-crm-writer`, `renewal-desk`), never as a side effect of a read
tool. This matches the Governance section: money data never auto-commits, and write
tiers require explicit authorization tokens (`APPROVE CRM ONLY`, `APPROVE SUPABASE ONLY`,
`APPROVE ALL`).

Alongside these runtime tools, Amy has ~30 **domain playbooks** (also listed by the
same endpoint under `domain_skills` — e.g. `carrier-appetite`, `renewal-review`,
`commercial-risk-intake`, `commission-reconciliation`) that describe *how* to carry out
a workflow rather than being directly callable tools.

---

## Microsoft Strategy

### SharePoint

**Purpose:** Knowledge Base

Stores:

- SOPs
- Carrier guides
- Training
- Licensing docs
- Templates

### Copilot Studio

**Purpose:** Amy Interface

Provides:

- AI orchestration
- Tool calling
- User experience
- Microsoft Copilot integration

### Power Automate

**Purpose:** Glue Layer

Connects:

- Supabase
- Zoho
- Momentum
- SharePoint

---

## Governance

Every AI action should be observable, auditable, and permission-aware. Amy should log retrievals, tool calls, proposed outputs, workflow triggers, and write actions. Lower-risk actions such as searching knowledge can be logged lightly, while higher-risk actions such as updating client data, creating tasks, sending communications, or triggering workflows should require stronger authorization and clearer audit trails.

### Identity & Permissions

"Permission-aware" needs a concrete identity model, not just risk tiers. The backend already provides the primitives: `hermes-api` authenticates callers with a bearer token (`HERMES_API_TOKEN`) and runs role-scoped services (for example `create_app("finance")`), and write actions require explicit approval tokens (`APPROVE CRM ONLY`, `APPROVE SUPABASE ONLY`, `APPROVE ALL`). The operating model should map:

- **Amy user → role** (e.g. Boss/Lamar, Personal Lines/Gretchen, service) → allowed read scopes and **write tier**.
- **Each write tool → minimum role + approval token** required to run it.
- **Every write → logged** with the acting identity, the tool, and the approval token used.

Copilot Studio should pass the authenticated user through to the MCP bridge so authorization is enforced in `hermes-api`, not in the UI.

### Data Handling & Compliance

The platform touches regulated data — client PII, and health-adjacent data through the group benefits and **Medicare** intake flows. The operating model should state, at minimum:

- **Classification:** what counts as PII/PHI and where it may live (system of record vs. Supabase index vs. logs).
- **Minimization in logs:** audit logs record *that* an action happened and by whom — not raw PII/PHI payloads.
- **Retention & deletion:** how long intelligence-layer copies persist, and how a deletion in the system of record propagates to mirrors.
- **Residency & access:** which vendors (Supabase, Microsoft 365, Nextcloud, the LLM provider) process which data, under least-privilege access.

This sits beside Governance because it constrains what Amy is allowed to retrieve, surface, and log.

---

## Development Principles

### Build

- SharePoint knowledge
- Amy
- Supabase integrations
- Power Automate workflows
- Client 360
- Carrier Intelligence
- Renewal Intelligence

### Avoid

- Dataverse as primary database
- Replacing Zoho
- Replacing Momentum
- Azure AI Foundry for now
- Multiple AI agents
- Microservices
- Creating more repositories

---

## Immediate Roadmap

### Phase 1: Knowledge Foundation

Move core agency knowledge from Obsidian into SharePoint, organize it by business function, and make it searchable by Amy.

### Phase 2: Read-Only Integrations

Connect Amy to Supabase, Zoho, Momentum or NowCerts, and SharePoint for safe retrieval before enabling write actions.

### Phase 3: Controlled Automation

Add approved workflows for renewals, commissions, client service, and intake. Each automation should include clear permissions, logging, and an escalation path when Amy lacks confidence.

---

## North Star

The end goal is simple:

> Amy becomes the single front door to Risk Solutions Group.

Nobody should have to remember whether information lives in SharePoint, Zoho, Momentum, Supabase, Outlook, Teams, CarrierHub, Commission Tracker, or another repository.

They simply ask:

```text
Amy, help me.
```

And Amy finds the answer from the appropriate system while preserving each platform as the source of truth.
