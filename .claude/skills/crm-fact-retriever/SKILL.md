---
name: crm-fact-retriever
description: Answer direct retrieval questions about RSG clients, prospects, contacts, policies, quotes, renewals, and notes — sourced from the CRM, the `client_facts` / `client_notes` / `quote_facts` retrieval tables, and indexed documents. Cites the source and confidence for every answer. Never invents data. Trigger for questions like "what is JB Noble's EIN?", "what is Joseph Washington's phone?", "what is 3D Pumps's renewal date?", "who is the principal for X?", "what quotes are pending on X?", "what policies does Y have?".
---

# CRM Fact Retriever

The agency-memory query skill. Answers concrete questions like:

```
What is JB Noble's EIN number?
What is Joseph Washington's phone number?
Who is the principal for 3D Pumps LLC?
What policies does this account need?
What was the proposed effective date?
What quotes were received?
What underwriting concerns were identified?
What is 3D Pumps's renewal date?
What life insurance opportunities are open?
```

## When to use

Use this skill whenever the user (or another skill) asks a **specific** fact
about a client, prospect, contact, policy, opportunity, renewal, quote, or
note. The signal is usually a "what / who / when / which / how much"
question scoped to a named entity.

Do **not** use this skill for:

- Drafting new records → `crm-intake-writer`
- Writing call notes → `crm-note-structurer`
- Renewal triage → `renewal-review`
- Carrier feedback → `carrier-appetite`

## Retrieval order — strict

Search sources in this order. Stop at the first confident answer; never
skip ahead.

1. **CRM canonical field** — direct lookup via the CRM MCP (`get_crm_record`,
   search-by-FEIN, search-by-email) for Account/Contact/Opportunity/Policy
2. **`client_facts`** — structured key/value facts indexed by entity.
3. **`client_notes`** — structured narrative notes (titles + summaries
   indexed; full body on demand).
4. **`client_documents`** — document summaries indexed; pull extracted text
   only when summary doesn't answer.
5. **`quote_facts`** / **`policy_facts`** — line-level quote and policy
   detail for premium, carrier, effective date, status questions.
6. **Full extracted document text** — last resort.

If still unknown after step 6: **say it is not found**. Do not infer, do
not guess, do not synthesize.

## Answer format

Always include source and confidence. Use this template:

```
{Entity}'s {fact_label} is {fact_value}.
Source: {source} ({source_date or "—"})
Confidence: {high | medium | low}
```

Examples:

```
JB Noble's EIN is XX-XXXXXXX.
Source: client_facts (underwriting summary, 2026-05-19)
Confidence: high

Joseph Washington's phone is (xxx) xxx-xxxx.
Source: CRM Contact.phoneNumber
Confidence: high

3D Pumps LLC's renewal date for General Liability is 2027-05-19.
Source: CRM Policy.expiration_date (carrier: Shield Commercial)
Confidence: high
```

When not found:

```
I do not have JB Noble's EIN in the CRM fields, client_facts, structured
notes, or indexed documents. Want me to open an intake to capture it?
```

When ambiguous (multiple matches):

```
There are two accounts that match "JB Noble":
  1. JB Noble Construction LLC (FEIN 12-3456789) — Active client
  2. JB Noble Benefits (no FEIN) — Group Benefits prospect
Which one do you mean?
```

When confidence is medium/low: state why (e.g. "extracted from PDF page 3,
field may have been OCR'd").

## Question → query map

| Question shape | Primary source | Field |
|----------------|---------------|-------|
| "What is X's EIN?" | Account → `client_facts` | `fein` / `EIN` |
| "What is X's phone?" | Contact / Account | `phoneNumber` |
| "What is X's email?" | Contact / Account | `emailAddress` |
| "Who is the principal of X?" | Contact via Account | `contactRole = Principal` |
| "What policies does X have?" | Policy by Account | list `policy_number`, `line_of_business`, `carrier` |
| "What is X's renewal date?" | Policy / Renewal | `expiration_date` |
| "What was the proposed effective date?" | Opportunity | `proposed_effective_date` / `closeDate` |
| "What quotes were received?" | `quote_facts` / Opportunity quotes | `quote_number`, `premium`, `carrier`, `status` |
| "What underwriting concerns?" | `client_notes` of type Underwriting Summary | `risk_flags` |
| "What policies does this account need?" | Opportunity pipeline (open) | per-LOB stages |
| "What life insurance opportunities are open?" | Opportunity where LOB=Life and stage∉{Closed Won, Closed Lost} | list |
| "Cross-sell opportunities for X?" | Account intel + open Opps | gaps in LOB coverage |

## Hard rules

1. **No invention.** If the fact isn't in a source, say so. The IRS already
   has enough drama.
2. **Cite source for every fact.** No source = no answer.
3. **Restricted data — answer carefully.** EIN, DOB, DL #, SSN, banking,
   beneficiary, health notes:
   - In a 1:1 direct question, answer with the value and mark
     `sensitivity: restricted`.
   - In a broad listing or Slack channel that isn't private, summarize
     ("EIN on file") and offer to DM the value.
4. **Read-only.** This skill never writes. If a fact is missing and the
   user wants to add it, hand off to `crm-intake-writer`.
5. **Page list calls.** Honor `MAX_LIST_SIZE = 200` from
6. **Walk relationships explicitly.** A Contact may belong to multiple
   Accounts; verify before answering ownership questions.
7. **Resolve fields via `SchemaRegistry.find_field()`** or the MCP
   metadata tool — never hardcode field casing (camelCase vs snake_case
   migration is live).

## Multi-source resolution

When CRM and `client_facts` disagree:

- If both have a value and they conflict → flag the conflict, prefer the
  CRM canonical field, and recommend opening a data-quality task.
- If CRM is empty and `client_facts` has it → answer from `client_facts`
  and recommend backfilling the CRM field (handoff: `crm-intake-writer`).
- If `client_facts` confidence is `low` → say so explicitly.

## References

- `docs/agency-memory-plan.md` — retrieval architecture
- `crm-intake-writer` — handoff target when a missing fact should be added
