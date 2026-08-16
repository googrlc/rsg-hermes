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
Zoho                = CRM System of Record
SharePoint          = Document & Knowledge System
Supabase            = Intelligence, Search, Analytics
Microsoft Copilot   = User Experience Layer
```

## Glossary

- **Amy:** The agency assistant and primary AI interface for Risk Solutions Group.
- **System of record:** The official source for a specific data type.
- **Intelligence layer:** The search, analytics, enrichment, and reasoning layer that helps Amy retrieve and interpret information.
- **Tool:** A behind-the-scenes capability Amy can call to retrieve information or trigger an approved workflow.
- **Write action:** Any action that creates, edits, sends, updates, or triggers something outside the AI conversation.

---

## Source-of-Truth Hierarchy

When systems disagree, Amy should defer to the system of record for that data type. Momentum or NowCerts controls policy and coverage data. Zoho controls CRM relationship data. SharePoint controls procedures, templates, training, and internal knowledge. Supabase supports search, classification, analytics, and intelligence, but should not override the official source systems.

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
                  (Copilot Studio)
                          |
       ┌──────────────────┼──────────────────┐
       |                  |                  |
   SharePoint          Supabase        Power Automate
   Knowledge         Intelligence      Orchestration
       |                  |                  |
       └──────────────────┼──────────────────┘
                          |
              Zoho CRM          Momentum AMS
                CRM              Policy Truth
```

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
- Future Amy headquarters

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

#### Semantic Search

Supports:

- AI knowledge retrieval
- Carrier retrieval
- Agency procedure retrieval

---

## Amy

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
