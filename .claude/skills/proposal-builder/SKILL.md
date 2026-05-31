---
name: proposal-builder
description: Build a structured client-facing or carrier-facing proposal packet — for a new submission, a renewal remarket, a side-by-side quote comparison, or a final binding presentation. Pulls account/contact/opportunity/quote data from EspoCRM + `quote_facts`, accepts the carrier slate from `carrier-appetite`, and produces a stack-rank table, coverage comparison, missing-info checklist, and a draft cover note. Never invents quote numbers, premiums, limits, or carrier offers. Use whenever the user says "build a proposal," "comparison for the client," "submission packet for carrier X," or "side-by-side for the renewal."
---

# Proposal Builder

The "package this up to send" skill. Consumes data already collected by
intake / carrier-appetite / quote ingestion and arranges it into the two
shapes producers actually send.

## When to use

- "Build a client proposal for 3D Pumps."
- "Side-by-side comparison of the three GL quotes."
- "Submission packet for AmTrust on JB Noble WC renewal."
- "Bind packet for Joseph Washington's home + auto."
- Renewal remarket — packet to send to carrier underwriters.

Do **not** use this skill for:

- New-business extraction → `commercial-risk-intake` /
  `personal-lines-intake` / `life-insurance-intake` / `benefits-intake`.
- Carrier selection → `carrier-appetite`.
- Note writing → `crm-note-structurer`.

## Two output modes

### A — Client-facing proposal

```json
{
  "mode": "client_proposal",
  "audience": "client",
  "sensitivity": "standard",
  "account_name": "3D Pumps LLC",
  "primary_contact": "Jarod Denero Mattison",
  "producer": "Lamar Coates",
  "proposed_effective_date": "2026-05-19",
  "delivery_format": "PDF | Slides | Email Inline",
  "sections": [
    {
      "id": "cover",
      "title": "Insurance Proposal — 3D Pumps LLC",
      "subtitle": "Prepared 2026-05-15 · Effective 2026-05-19 · Lamar Coates, Risk Solutions Group",
      "body": "..."
    },
    {
      "id": "exec_summary",
      "title": "Summary",
      "bullets": [
        "Six lines of business proposed: GL, WC, Auto, Inland Marine, Pollution, Umbrella.",
        "Total annual estimate: $X — see breakdown.",
        "Two of six lines have firm quotes; remaining four pending market response."
      ]
    },
    {
      "id": "coverage_table",
      "title": "Coverage Summary",
      "columns": ["Line", "Carrier", "Quote #", "Premium", "Fees", "Total", "Effective", "Status"],
      "rows": [
        ["General Liability", "Shield Commercial", "656137", 1533.00, 477.32, 2010.32, "2026-05-19", "Firm quote"],
        ["Inland Marine", "Shield Commercial", "656139", 450.00, 18.00, 468.00, "2026-05-19", "Firm quote"],
        ["Workers Compensation", "AmTrust (pending)", null, null, null, null, "2026-05-19", "Submission in progress"]
      ]
    },
    {
      "id": "coverage_detail",
      "title": "Coverage Detail",
      "lobs": [
        {
          "lob": "General Liability",
          "limits": "1M / 2M",
          "deductible": 0,
          "key_endorsements": ["Additional Insured by written contract", "Waiver of Subrogation by written contract"],
          "exclusions_to_note": ["Pollution — see CPL submission"]
        }
      ]
    },
    {
      "id": "what_we_recommend",
      "title": "Recommendation",
      "body": "Bind GL and Inland Marine now to secure quoted rates; advance WC + Auto + Pollution + Umbrella once carrier responses received."
    },
    {
      "id": "what_is_pending",
      "title": "What's Pending",
      "checklist": [
        "Loss runs (5-year, currently valued)",
        "Driver MVRs",
        "Signed ACORD 125/126"
      ]
    },
    {
      "id": "next_steps",
      "title": "Next Steps",
      "body": "Confirm acceptance of GL and Inland Marine by 05/17/2026. Pollution and WC market feedback expected by 05/22/2026."
    },
    {
      "id": "disclosures",
      "title": "Disclosures",
      "body": "This proposal is a summary; the bound policy controls. Coverages, limits, and exclusions described here are subject to change by the carrier."
    }
  ]
}
```

### B — Carrier submission packet

```json
{
  "mode": "carrier_submission",
  "audience": "carrier_underwriter",
  "sensitivity": "restricted",
  "account_name": "JB Noble Construction LLC",
  "carrier": "AmTrust",
  "lob": "Workers Compensation",
  "submission_type": "Renewal Remarket",
  "submission_date_target": "2026-12-15",
  "sections": [
    {
      "id": "cover_letter",
      "title": "Submission — JB Noble Construction LLC — WC Remarket",
      "body": "..."
    },
    {
      "id": "risk_summary",
      "title": "Risk Summary",
      "bullets": [
        "Construction class 5403 — bypass and excavation pump installation",
        "$80K payroll, 2 employees + occasional sub use",
        "GA primary, occasional AL/FL work — confirm filings"
      ]
    },
    {
      "id": "exposure_table",
      "title": "Exposures",
      "rows": [
        {"class_code": "5403", "description": "Carpentry — Construction of Residential Property", "payroll": 80000, "state": "GA"}
      ]
    },
    {
      "id": "loss_history",
      "title": "Loss History",
      "summary": "5-year loss runs attached. 0 claims, 0 incurred.",
      "attachments": ["loss-runs-2022-2026.pdf"]
    },
    {
      "id": "current_program",
      "title": "Current Program",
      "rows": [
        {"carrier": "Travelers", "policy_number": "POL-12345", "premium": 18420, "effective": "2026-01-01", "expiration": "2027-01-01"}
      ]
    },
    {
      "id": "what_we_need",
      "title": "What We're Asking",
      "bullets": [
        "WC indication for 2027-01-01 effective",
        "Confirm appetite for class 5403 with subcontracted excavation < 10%",
        "Owner inclusion preferences"
      ]
    },
    {
      "id": "attachments",
      "title": "Attachments",
      "files": [
        {"name": "ACORD 130 — WC App", "type": "application"},
        {"name": "Loss Runs 2022-2026", "type": "loss_runs"},
        {"name": "Experience Mod Worksheet", "type": "emr"}
      ]
    },
    {
      "id": "broker_contact",
      "title": "Broker Contact",
      "body": "Lamar Coates · Risk Solutions Group · lamar@rsg.example · (xxx) xxx-xxxx"
    }
  ]
}
```

## Workflow

1. **Pull source data** — Account, Contacts, Opportunity (or
   Opportunities for a multi-line package), Quotes / `quote_facts`,
   Loss runs / claims, Current policies.
2. **Determine mode** — client vs. carrier. Different sensitivity,
   different audience, different sections.
3. **Stack-rank the carriers** (client mode, multi-quote scenario)
   based on total cost and coverage match; flag any material exclusion
   differences explicitly.
4. **Reuse, do not synthesize** — quote numbers, premiums, limits, and
   carrier names come from real records only.
5. **Surface gaps** — missing applications, missing loss runs, missing
   signatures. The proposal isn't done until pending items are listed.
6. **Disclosures** — every client proposal carries the "policy
   controls" disclosure.

## Hard rules

1. **Never invent a quote.** No fake quote numbers, premiums, fees,
   limits, deductibles, or endorsements. If a row has no firm quote,
   mark it `"Submission in progress"` with null financials.
2. **Never compare quotes you didn't receive.** No "AmTrust is probably
   around $X" rows.
3. **Client mode = `sensitivity: standard`. Carrier mode = `restricted`.**
   Carrier submissions include loss runs and EMR — don't post in broad
   Slack channels.
4. **Disclosures are mandatory on client proposals.** "This proposal is
   a summary; the bound policy controls."
5. **Material exclusion = call it out.** If two GL quotes differ on a
   pollution exclusion, that goes in the comparison, not in the
   footnote.
6. **Effective date discipline.** Use the same `proposed_effective_date`
   across all LOBs in a package unless the source explicitly says
   otherwise.
7. **No rate prediction.** This skill does not estimate "likely
   renewal rate" or "expected discount." That's a quote, not a
   proposal.

## Companion outputs

The proposal builder can emit:

- A draft `client_documents` row pointing at the rendered PDF location
  (once the renderer writes it).
- A draft `client_notes` row with `note_type: "Quote Summary"` for the
  CRM — hand to `crm-note-structurer` for the body.
- A draft Task on the relevant Opportunity for "Send proposal" /
  "Follow up on proposal" — hand to `crm-intake-writer` for assembly.

## References

- `docs/agency-memory-plan.md`
- `docs/hermes-builder-spec.md` — Documents/Transcript workflows
- `carrier-appetite` — feeds the carrier slate
- `commercial-risk-intake`, `personal-lines-intake`,
  `life-insurance-intake`, `benefits-intake` — feed the risk profile
- `crm-note-structurer` — bodies for the CRM-side Quote Summary note
- `crm-intake-writer` — assembles any opportunity/note updates that the
  proposal triggers

## Save to the document library

When the proposal/comparison packet is final, persist it so it appears in
**Agent OS → Documents** (under the client's folder) and the agent's
Holographic Memory (Supermemory + Google Drive mirror):

```bash
hermes --doc-add \
  --doc-title "<client> — <LOB> Proposal" \
  --doc-account "<EspoCRM account name>" \
  --doc-type proposal \
  --doc-file <path>          # or pipe the document via stdin
```

Use `--doc-type comparison` for a side-by-side. Or POST `/api/documents/save`
to the Hermes API: `{ "title", "content", "account_name", "doc_type":
"proposal", "source": "proposal-builder" }`. `account_name` = the client's
EspoCRM account so it files under that client's folder.
