---
name: crm-intake-writer
description: Turn any insurance-related summary, document, transcript, email, quote proposal, or Slack note into a structured, duplicate-safe CRM intake payload (Account + Contacts + per-LOB Opportunities + Note + Facts) for EspoCRM. Drafts the payload and asks for an approval token — never writes directly. Trigger whenever the user pastes an intake summary, says "put this into CRM," "draft an intake," "create the account for...", or attaches an underwriting/quote/transcript document for ingestion.
---

# CRM Intake Writer

The author skill for any CRM intake. This skill **prepares** a complete intake
payload — it never writes. Writes are mediated by the `crm-upsert-planner`
skill, the `crm_write_queue` worker, or an explicit n8n webhook, only after
the user returns an approval token.

## When to use

Use this skill when:

- The user pastes an underwriting summary, quote proposal, application,
  transcript, fact-finder, loss run, driver/vehicle schedule, or any
  client-facing intake text.
- The user says "put this in CRM", "draft intake for…", "build the account
  for…", "stage the opportunity for…", "intake this".
- Another skill (e.g. `commercial-risk-intake`, `personal-lines-intake`,
  `life-insurance-intake`, `benefits-intake`) needs a unified output envelope.
- An email or Slack message is forwarded that describes a real prospect,
  client, or service request that should land in EspoCRM.

Do **not** use this skill for:

- Pure retrieval questions ("what is X's EIN?") — use `crm-fact-retriever`.
- Pure note-only requests — use `crm-note-structurer`.
- Renewal triage — use `renewal-review`.

## Core contract

Every intake produces TWO artifacts, in this order:

1. **CRM records** (Account, Contacts, Opportunities, Note) — staged for write.
2. **Retrieval facts** (`client_facts`, `client_notes`, `quote_facts`) —
   indexed for later question-answering by `crm-fact-retriever`.

Never produce only one. The CRM is the system of record; retrieval is the
memory layer.

## Workflow

### 1. Classify the intake

Identify one or more of:

- `Commercial Account`, `Personal Lines Household`, `Life Insurance Prospect`,
  `Group Benefits Prospect`, `Medicare Prospect`, `Renewal`,
  `Service Request`, `Claim`, `Quote Summary`, `Underwriting Submission`,
  `Carrier Appetite Note`.

Identify line(s) of business from the canonical vocabulary in
`hermes-training/espocrm/workflows.md` (General Liability, Workers Comp,
Commercial Auto, BOP, Inland Marine, Pollution, Professional, Cyber,
Umbrella, Home, Auto, Dwelling Fire, Life, Disability, Medicare, Group
Health, Dental, Vision, Supplemental).

### 2. Extract entities

Delegate the extraction shape to the appropriate specialty intake skill when
relevant:

- Commercial / business → `commercial-risk-intake`
- Personal lines / household → `personal-lines-intake`
- Life / disability → `life-insurance-intake`
- Group / Medicare / benefits → `benefits-intake`

Each returns a normalized account + contacts + facts block. This skill
assembles the final payload.

### 3. Search before create

Before producing the final payload, populate `duplicate_search` so the
upserter/n8n can dedupe. Always search by:

- **Account:** legal name, DBA, FEIN, phone, email, street address,
  principal/contact name.
- **Contact:** email, phone, full name, name + account.
- **Opportunity:** account + LOB + effective/target date, quote number(s).

Do not invent IDs. If a probable duplicate exists, mark
`action: "needs_human_review"` and explain why.

### 4. Build per-LOB opportunities

**Hard rule:** Create one Opportunity per line of business. Do **not**
bundle GL + WC + Auto + Inland Marine + Pollution + Umbrella into a single
"Commercial Package" opportunity. The only exception is when the user
explicitly asks for a package-level tracking opportunity in addition to
per-LOB rows.

Opportunity naming format:

```
[Account Name] - [Line of Business] - [MM/DD/YYYY effective or target]
```

Examples:

```
3D Pumps LLC - General Liability - 05/19/2026
3D Pumps LLC - Workers Compensation - 05/19/2026
Joseph Washington Household - Home & Auto - 2026
JB Noble - Group Health Renewal - 01/01/2027
```

Each opportunity must include:

- `account_name`
- `primary_contact`
- `opportunity_name`
- `line_of_business` (single value)
- `stage` (use the canonical enum — see `hermes-training/espocrm/guardrails.md`)
- `proposed_effective_date` or `target_date`
- `producer`
- `opportunity_type` (`New Business`, `Renewal`, `Cross-Sell`, `Remarket`)
- `quote_number` if available
- `carrier` if available
- `premium`, `fees`, `total` if available
- `status`
- `package_name` or `related_submission_name` when part of a multi-line
  submission

Stage defaults when only some LOBs are quoted:

| Situation | Stage |
|-----------|-------|
| Quote number present | `Quote / Market Review` (Espo: `Quoting`) |
| LOB requested but no quote yet | `Market / Submission Needed` (Espo: `Discovery`) |
| Carrier already declined | `Closed Lost` with reason |
| Bound | `Closed Won` with policy reference |

When the canonical enum (`Discovery`, `Quoting`, `Markets Out / Shopping`,
`Proposal Presented`, `Negotiation`, `Closed Won`, `Closed Lost`) doesn't
have a 1:1 match, pick the closest forward-only stage and note the mapping
in `notes`.

### 5. Build the unified payload

Emit JSON matching this shape exactly. Required fields are starred in
comments; omit anything you don't have rather than guessing.

```json
{
  "action": "crm_intake_upsert",
  "approval_required": true,
  "source": {
    "type": "slack_summary | document | email | transcript | quote_proposal | manual",
    "submitted_by": "Lamar Coates",
    "date": "YYYY-MM-DD",
    "source_ref": "optional URL, file name, or message_ts"
  },
  "classification": ["Commercial Account", "Underwriting Submission"],
  "lines_of_business": ["General Liability", "Workers Compensation"],

  "account": {
    "account_name": "3D Pumps LLC",
    "legal_name": "3D Pumps LLC",
    "dba": null,
    "fein": "33-3725730",
    "entity_type": "LLC",
    "industry": "Construction",
    "naics": null,
    "address": "503 S Evelyn Pl NW",
    "city": "Atlanta",
    "state": "GA",
    "zip": "30318",
    "phone": null,
    "email": null,
    "website": null,
    "operations_summary": "Bypass pumping for water/wastewater treatment plants",
    "annual_revenue": 335000,
    "estimated_payroll": 80000,
    "employee_count": null,
    "account_type": "Prospect",
    "account_status": "Urgent",
    "tags": ["prospect", "commercial", "contractor", "water-infrastructure"]
  },

  "contacts": [
    {
      "full_name": "Jarod Denero Mattison",
      "first_name": "Jarod",
      "last_name": "Mattison",
      "role": "Sole Member",
      "household_role": null,
      "phone": "(770) 780-8848",
      "email": "jarod.mattison@gmail.com",
      "relationship_to_account": "Principal",
      "primary_contact": true
    }
  ],

  "opportunities": [
    {
      "opportunity_name": "3D Pumps LLC - General Liability - 05/19/2026",
      "line_of_business": "General Liability",
      "stage": "Quoting",
      "quote_number": "656137",
      "carrier": "Shield Commercial",
      "premium": 1533.00,
      "fees": 477.32,
      "total": 2010.32,
      "proposed_effective_date": "2026-05-19",
      "opportunity_type": "New Business",
      "producer": "Lamar Coates",
      "package_name": "3D Pumps LLC - Commercial Insurance Submission - 05/19/2026",
      "tags": ["small-contractors-gl"]
    }
  ],

  "note": {
    "title": "3D Pumps LLC - Underwriting Summary",
    "note_type": "Underwriting Summary",
    "body": "Structured summary produced by crm-note-structurer",
    "tags": ["underwriting", "prospect"]
  },

  "facts": [
    {
      "entity": "3D Pumps LLC",
      "entity_type": "Account",
      "fact_label": "EIN",
      "fact_value": "33-3725730",
      "sensitivity": "restricted",
      "source": "underwriting summary"
    },
    {
      "entity": "Jarod Denero Mattison",
      "entity_type": "Contact",
      "fact_label": "Phone",
      "fact_value": "(770) 780-8848",
      "sensitivity": "standard",
      "source": "underwriting summary"
    }
  ],

  "duplicate_search": {
    "account": ["3D Pumps LLC", "3D Pumps", "33-3725730", "503 S Evelyn Pl NW"],
    "contacts": ["Jarod Denero Mattison", "jarod.mattison@gmail.com", "(770) 780-8848"],
    "opportunities": ["3D Pumps LLC + General Liability + 2026-05-19", "656137"]
  },

  "approval_tokens": [
    "APPROVE ALL",
    "APPROVE CRM ONLY",
    "APPROVE SUPABASE ONLY",
    "APPROVE TASKS ONLY",
    "REVISE",
    "CANCEL"
  ],
  "write_status": "NOT_WRITTEN_AWAITING_CONFIRMATION"
}
```

### 6. Render a human approval prompt

After the JSON, render a short readable summary:

```
Intake draft ready — NOTHING WRITTEN YET.

Account:       3D Pumps LLC (LLC, Construction)
Contacts:      Jarod Denero Mattison (Principal)
Opportunities: 6 — one per LOB (GL, WC, Auto, Inland Marine, Pollution, Umbrella)
Note:          Underwriting Summary
Facts staged:  9 (1 restricted: EIN)

Possible duplicates checked: account name, FEIN, address, principal name.

Reply with one of:
  APPROVE ALL · APPROVE CRM ONLY · APPROVE SUPABASE ONLY · APPROVE TASKS ONLY · REVISE · CANCEL
```

## Hard rules

1. **Never write.** This skill produces drafts. Writes only happen after an
   approval token is returned and routed through `crm-upsert-planner` →
   `crm_write_queue` → worker → `crm_receipts`.
2. **One opportunity per LOB.** Never bundle.
3. **Search first.** Always populate `duplicate_search`; never create when a
   confident match exists — mark `needs_human_review` instead.
4. **Snake_case for new fields.** Existing camelCase fields stay until
   migrated; see `espocrm-field-reference`.
5. **No invented EINs, policy numbers, quote numbers, premiums, or DOBs.**
   If the source doesn't contain it, leave it null.
6. **Mark sensitivity.** EIN, DOB, DL #, SSN, health notes, beneficiary
   info, banking info → `sensitivity: "restricted"`. Do not echo restricted
   values back into broad Slack summaries.
7. **Always emit facts.** Every intake creates `client_facts` rows for
   future retrieval — that's how the agency memory works.

## Related skills

- `crm-fact-retriever` — answers "what is X's Y?" using facts + CRM.
- `crm-note-structurer` — formats the `note.body` portion of the payload.
- `crm-upsert-planner` — converts an approved payload into specific
  EspoCRM API calls / `crm_write_queue` rows.
- `commercial-risk-intake`, `personal-lines-intake`, `life-insurance-intake`,
  `benefits-intake` — specialty extractors.

## References

- `docs/agency-memory-plan.md` — full plan and rationale
- `docs/hermes-builder-spec.md` — confirm-before-write contract
- `docs/hermes-router-contract.md` — unified specialist envelope
- `hermes-training/espocrm/guardrails.md` — enum vocab and write safety
- `hermes-training/espocrm/field_dictionary.md` — field types and enums
- `hermes/commands/intake.py` — existing lightweight intake handler
