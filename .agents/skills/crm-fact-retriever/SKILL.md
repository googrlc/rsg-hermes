---
name: crm-fact-retriever
description: Answer direct retrieval questions about RSG clients, prospects, contacts, policies, quotes, renewals, and notes — sourced from the CRM, the `client_facts` / `client_notes` / `quote_facts` retrieval tables, and indexed documents. Cites the source and confidence for every answer. Never invents data. Trigger for questions like "what is JB Noble's EIN?", "what is Joseph Washington's phone?", "what is 3D Pumps's renewal date?", "who is the principal for X?", "what quotes are pending on X?", "what policies does Y have?".
---

# CRM Fact Retriever

The agency-memory query skill. **It has a real runtime executor** —
[hermes/commands/fact_retriever.py](../../../hermes/commands/fact_retriever.py),
whose own docstring calls itself "runtime executor for the `crm-fact-retriever`
skill." Prefer calling it over hand-querying:

| Path | How |
|---|---|
| HTTP | `POST /agency-fact` — `{"question": "What is JB Noble's EIN?"}` or `{"entity": "JB Noble", "fact_label": "EIN"}` |
| NL agent | dispatcher handler `agency_fact` |
| Direct | `fact_retriever.parse_question()` then `fact_retriever.retrieve()` |

`parse_question` maps natural phrasing to a canonical `fact_label` (EIN, Date of
Birth, Email, Phone, …). A 400 means it couldn't parse the label — rephrase as
"What is <entity>'s <thing>?" or pass `entity` + `fact_label` directly.

Answers concrete questions like:

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

This is the order the executor implements. Stop at the first confident answer.

| # | Source | Rows (2026-07-26) |
|---|---|---|
| 1 | **Canonical book** — `canonical_clients` / `canonical_policies` | 415 / 618 |
| 2 | **`client_facts`** — key/value facts by entity | **11** |
| 3 | **`client_notes`** — summary, then full text | **2** |
| 4 | **`client_documents`** — summary, then extracted text | **0** |
| 5 | **`quote_facts`** — per-quote financial detail | **0** |
| 6 | **`policy_facts`** — per-policy detail | **0** |

> **Steps 4–6 are empty and step 3 is nearly so.** In practice this skill
> answers from the canonical book plus 11 facts. Do not tell anyone the agency
> has a populated memory layer — it does not yet. When a fact isn't found, that
> is the normal case, not a failure, and the right move is to offer an intake.

For identity facts the AMS outranks all of it: `mcp__rsg-hermes__ams_search_insured`
is the system of record for who a client is. If the book and NowCerts disagree,
**NowCerts wins.**

If still unknown: **say it is not found**. Do not infer, guess, or synthesize.

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
Source: NowCerts insured record
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
| "What is X's EIN?" | AMS insured, then `client_facts` | `fein` (label `EIN`) |
| "What is X's phone / email?" | AMS insured, then `client_facts` | `Phone` / `Email` |
| "Who is the principal of X?" | `client_facts` / `client_entities` | principal/contact label |
| "What policies does X have?" | `canonical_policies` by insured GUID | `policy_number`, `lines_of_business`, `carrier` |
| "What is X's renewal date?" | `canonical_policies` → `renewal_candidates` | `expiration_date` / `renewal_event_date` |
| "What quotes were received?" | `opportunities` (+ `opportunity_quotes`) | `quote_number`, `nowcerts_quote_guid`, `carrier`, `premium_estimate` |
| "What underwriting concerns?" | `client_notes` | narrative body |
| "What's open for X?" | `opportunities` where `status='open'` | `line_of_business`, `stage` |
| "Cross-sell gaps for X?" | `GET /api/cross-sell` | current LOBs vs. missing |

**Stage vocabulary, if you filter on it:** open means `status='open'`. The
terminal stages are `Bound / Won` and `Lost` (new business), `Complete/Auto-Renewal`
and `Not Renewed` (renewal). There is no `Closed Won` / `Closed Lost` — that was
the Espo vocabulary and it will match nothing.

Note `opportunity_quotes` has **0 rows**, so quote-detail questions currently
resolve from the opportunity row itself.

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
5. **Cap list calls at 200 rows** and page beyond that.
6. **Walk relationships explicitly.** A client may have several policies and
   the book has known duplicate clients — verify before answering an ownership
   question.
7. **Prefer the executor over hand-built queries.** It already encodes the
   hierarchy, the citation, and the confidence. Re-deriving it by hand is how
   the two drift apart.

## Multi-source resolution

When CRM and `client_facts` disagree:

- If both have a value and they conflict → flag the conflict, prefer the
  CRM canonical field, and recommend opening a data-quality task.
- If CRM is empty and `client_facts` has it → answer from `client_facts`
  and recommend backfilling the CRM field (handoff: `crm-intake-writer`).
- If `client_facts` confidence is `low` → say so explicitly.

## References

- `hermes/commands/fact_retriever.py` — **the executor**; the hierarchy lives here
- `hermes/integrations/retrieval_client.py` — the retrieval-table reads
- `POST /agency-fact` (`hermes/api.py`) — the HTTP entry point
- the `rsg-hermes` MCP door — `ams_search_insured`, `list_renewals`, `list_documents`
- `crm-intake-writer` — handoff when a missing fact should be captured
- `docs/agency-memory-plan.md` — original architecture (transport sections are historical)
