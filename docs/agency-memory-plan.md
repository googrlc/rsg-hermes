# Hermes Agency Memory + CRM Writing + Retrieval — Plan

> Goal: turn every insurance-related summary, document, transcript,
> quote, email, or Slack message into structured CRM records **and**
> retrievable knowledge — so Hermes can both keep EspoCRM tidy and
> answer "what is JB Noble's EIN?" later without inventing data.

This document is the index for the agency-memory skill family. The
skills themselves live under `.claude/skills/` and each carries its own
frontmatter + playbook.

---

## 1. Objective

Hermes must be able to answer questions like:

```
What is JB Noble's EIN number?
What is Joseph Washington's phone number?
Who is the principal for 3D Pumps LLC?
What policies does this account need?
What was the proposed effective date?
What quotes were received?
What underwriting concerns were identified?
```

…across **commercial, personal, life, benefits, service, renewal**, and
**claims** work — while also writing the corresponding records into
EspoCRM with no duplicates, no fabricated data, and no skipped pipeline
stages.

---

## 2. Four jobs, not one

Every intake produces both:

1. **CRM records** — Account, Contacts, per-LOB Opportunities, ClientNote.
2. **Retrieval knowledge** — `client_facts`, `client_notes`,
   `quote_facts`, `client_documents` rows that make the same data
   queryable later.

The CRM is the **system of record**. The retrieval tables are the
**memory layer**.

---

## 3. Skill map

All skills live under `.claude/skills/<name>/SKILL.md`.

### Authoring & retrieval (cross-cutting)

| Skill | Job |
|-------|-----|
| `crm-intake-writer` | Unified intake payload builder (Account + Contacts + per-LOB Opportunities + Note + Facts), confirm-before-write. |
| `crm-note-structurer` | Canonical CRM ClientNote body format. |
| `crm-fact-retriever` | Answers direct retrieval questions, cites source + confidence, never invents. |
| `crm-upsert-planner` | Turns an approved payload into an ordered, deterministic write plan with duplicate searches first. |

### Specialty intake extractors

| Skill | Domain |
|-------|--------|
| `commercial-risk-intake` | Businesses — GL, WC, Auto, Property, IM, Pollution, Pro, Cyber, Umbrella. |
| `personal-lines-intake` | Households — Home, Auto, Umbrella, Renters, Dwelling Fire, Boat/RV. |
| `life-insurance-intake` | Term, Whole, Universal, Final Expense, individual DI. |
| `benefits-intake` | Group benefits + Medicare (two output shapes). |

### Operational

| Skill | Job |
|-------|-----|
| `renewal-review` | Project 85 renewal triage — risk class, recommendation, action drafts. |
| `carrier-appetite` | Match risks to RSG-appointed carriers, grounded in `carrier_appetite` tables. |
| `proposal-builder` | Client-facing proposals and carrier submission packets. |
| `revenue-sentinel` | Operator playbook for the daily Project 85 sentinel briefing and Slack triage. |

---

## 4. Workflow shape

```
Raw input  (Slack, email, PDF, transcript, fact-finder, quote proposal)
   │
   ▼
Specialty extractor                            commercial-risk-intake
(by domain)                                    personal-lines-intake
                                               life-insurance-intake
                                               benefits-intake
   │
   ▼
crm-intake-writer
   ├──► classification, lines of business
   ├──► duplicate_search bundle
   ├──► per-LOB Opportunities (NEVER bundled)
   ├──► ClientNote (delegates body to crm-note-structurer)
   └──► facts[] (for client_facts / quote_facts / policy_facts)
   │
   ▼
USER returns approval token  (APPROVE ALL | CRM ONLY | SUPABASE ONLY | TASKS ONLY | REVISE | CANCEL)
   │
   ▼
crm-upsert-planner
   ├──► Search every entity first
   ├──► CREATE_NEW | UPDATE_EXISTING | ATTACH_ONLY | NEEDS_HUMAN_REVIEW | SKIP
   ├──► Account → Contacts → Opportunities → Note → Facts
   └──► Transport: n8n webhook OR crm_write_queue OR supabase_insert
   │
   ▼
crm_write_queue worker  →  EspoCRM API  →  crm_receipts
Supabase insert         →  client_facts / client_notes / quote_facts / client_documents
   │
   ▼
Confirmation summary back to Slack
```

For retrieval the flow is much simpler:

```
User question
   │
   ▼
crm-fact-retriever
   ├──► 1. CRM canonical field
   ├──► 2. client_facts
   ├──► 3. client_notes
   ├──► 4. client_documents
   ├──► 5. quote_facts / policy_facts
   └──► 6. Full extracted document text
   │
   ▼  (stop at first confident answer)
Answer  +  Source  +  Confidence
   │
   ▼  (if missing)
Suggest crm-intake-writer to capture
```

---

## 5. CRM record strategy

### Account naming

| Domain | Naming pattern | Example |
|--------|---------------|---------|
| Commercial | Legal entity name | `3D Pumps LLC` |
| Personal Lines | `<Last> Household` (or full name when single) | `Joseph Washington Household` |
| Life | `<Name> Life Insurance` | `Joseph Washington Life Insurance` |
| Group Benefits | `<Company> Benefits` | `JB Noble Benefits` |
| Medicare | Individual name | `Walter Brooks` |

### Opportunity rule (hard)

**One Opportunity per Line of Business.** Never bundle.

Format:

```
[Account Name] - [Line of Business] - [MM/DD/YYYY effective or target]
```

Example for one commercial account:

```
3D Pumps LLC - General Liability - 05/19/2026
3D Pumps LLC - Workers Compensation - 05/19/2026
3D Pumps LLC - Commercial Auto - 05/19/2026
3D Pumps LLC - Inland Marine - 05/19/2026
3D Pumps LLC - Contractors Pollution Liability - 05/19/2026
3D Pumps LLC - Umbrella / Excess - 05/19/2026
```

A package-level "submission" identifier (`package_name`) lets every
per-LOB row reference the larger effort without collapsing the pipeline
reporting back into a junk drawer.

### Stage discipline

Use only the canonical enums in
`hermes-training/espocrm/guardrails.md`:

- Opportunity: `Discovery → Quoting → Markets Out / Shopping →
  Proposal Presented → Negotiation → Closed Won | Closed Lost`
- Renewal: `Identified → Outreach Sent → Quote Requested → Proposal
  Sent → Negotiating → Renewed - Won | Lost`

Default mappings inside intakes:

| Situation | Stage |
|-----------|-------|
| Quote number present | `Quoting` |
| LOB in scope, no quote yet | `Discovery` |
| Carrier declined | `Closed Lost` with reason |
| Bound | `Closed Won` |

---

## 6. Retrieval layer (planned tables)

These tables are referenced by `crm-fact-retriever`,
`crm-upsert-planner`, and the specialty intakes. They will land in a
follow-up Supabase migration:

| Table | Purpose |
|-------|---------|
| `client_facts` | Structured key/value facts per entity (EIN, phone, DOB, payroll, etc.) with source + confidence + sensitivity. |
| `client_notes` | Structured narrative notes — same shape as the ClientNote body produced by `crm-note-structurer`. |
| `client_documents` | Document references — file name, type, storage URL, extracted-text URL, summary, tags. |
| `client_entities` | Cross-system entity index — links CRM Account/Contact/Opportunity IDs to canonical retrieval IDs. |
| `client_relationships` | Person↔Entity links (Principal, Spouse, Beneficiary, Decision Maker). |
| `quote_facts` | Per-quote details — number, line, carrier, premium, fees, total, effective, status. |
| `policy_facts` | Per-policy details — number, line, carrier, premium, effective, expiration, status. |
| `underwriting_facts` | Risk / exposure facts that drive carrier appetite (class codes, payroll, vehicles, exposures, losses). |

This PR does **not** create the tables — it creates the skills that
will use them. Schema lands separately so it can be reviewed on its
own.

---

## 7. Write safety & approval

Default mode for every intake:

```
Draft  →  Search  →  Show payload  →  Ask approval  →  Write  →  Confirm
```

Allowed approval tokens (from `docs/hermes-builder-spec.md`):

```
APPROVE ALL
APPROVE CRM ONLY
APPROVE SUPABASE ONLY
APPROVE TASKS ONLY
REVISE
CANCEL
```

Hermes asks approval before:

- Creating an Account / Contact / Opportunity / Note / Task
- Updating any existing CRM field
- Adding sensitive personal data
- Advancing Opportunity / Renewal stage
- Marking won/lost
- Adding Policy records
- Sending client messages

All actual writes go through `crm_write_queue` → worker →
`crm_receipts` (or direct Supabase insert for retrieval tables). No
skill writes from a completion.

---

## 8. Sensitivity rules

Mark these `sensitivity: "restricted"` always:

```
EIN
DOB
Driver License #
Health information / diagnoses / Rx / lab values
Life insurance medical notes
Beneficiary information
SSN
Banking / payment information
VIN
```

Restricted values **may** appear in:

- The CRM (system of record).
- `client_facts` rows marked restricted.
- Internal `audience: "internal"` notes.
- 1:1 direct retrieval answers.

Restricted values **must not** appear in:

- Broad Slack summaries / channels.
- Client-facing proposals (unless explicitly required, e.g. DOB on a
  life proposal page sent to the client).

---

## 9. Tooling options (future work)

The skills are tool-agnostic; the actual write path will be one of:

- **Option A — n8n webhook layer.** Hermes posts JSON to n8n
  (`/search-account`, `/upsert-account`, `/create-fact`, …); n8n
  talks to EspoCRM and Supabase. Best near-term option;
  see `n8n-developer`.
- **Option B — direct EspoCRM API.** Faster but riskier; guardrails
  must be strict.
- **Option C — `rsg-crm-mcp` MCP server.** Cleanest long-term
  architecture once tools stabilize:
  `crm.searchAccount`, `crm.searchContact`,
  `crm.searchOpportunity`, `crm.upsertAccount`, `crm.upsertContact`,
  `crm.upsertOpportunity`, `crm.createNote`, `crm.searchFacts`,
  `crm.createFact`, `crm.attachDocumentSummary`.

Whichever transport ships, `crm-upsert-planner` emits the same plan;
only the `transport.type` value changes.

---

## 10. Build order

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 1 | Planning / payload skills (`crm-intake-writer`, `crm-fact-retriever`, `crm-note-structurer`, `crm-upsert-planner`, specialty intakes) | **Done (this PR)** |
| 2 | Operational skills (`renewal-review`, `carrier-appetite`, `proposal-builder`, `revenue-sentinel`) | **Done (this PR)** |
| 3 | Supabase retrieval tables (`client_facts`, `client_notes`, `client_documents`, `quote_facts`, `policy_facts`, `client_entities`, `client_relationships`, `underwriting_facts`) | Pending — separate migration PR |
| 4 | n8n webhook layer (`/search-*`, `/upsert-*`, `/create-fact`, `/search-facts`) | Pending |
| 5 | CRM write approval loop wired end-to-end through `crm_write_queue` + worker + Slack confirmations | In progress (worker exists; planner output not yet consumed) |
| 6 | Retrieval / query exercises against real CRM data | Pending |

---

## 11. References

- `.claude/skills/crm-intake-writer/SKILL.md`
- `.claude/skills/crm-note-structurer/SKILL.md`
- `.claude/skills/crm-fact-retriever/SKILL.md`
- `.claude/skills/crm-upsert-planner/SKILL.md`
- `.claude/skills/commercial-risk-intake/SKILL.md`
- `.claude/skills/personal-lines-intake/SKILL.md`
- `.claude/skills/life-insurance-intake/SKILL.md`
- `.claude/skills/benefits-intake/SKILL.md`
- `.claude/skills/renewal-review/SKILL.md`
- `.claude/skills/carrier-appetite/SKILL.md`
- `.claude/skills/proposal-builder/SKILL.md`
- `.claude/skills/revenue-sentinel/SKILL.md`
- `docs/hermes-operating-constitution.md`
- `docs/hermes-builder-spec.md`
- `docs/hermes-router-contract.md`
- `docs/hermes-supabase-domain-map.md`
- `hermes-training/espocrm/`
