---
name: renewal-review
description: Triage an upcoming renewal — pull the Policy + Renewal + Account context, compute the increase math, classify risk, recommend remarket vs. retain, and propose the next action (call, email, remarket submission, escalation). Operates against EspoCRM Renewal + Policy + Account data and the Supabase `project_85_renewals` + `renewal_actions` tables. Produces a renewal-review payload + drafted `renewal_actions` rows and (when appropriate) a Cross-Sell / Remarket Opportunity draft for `crm-intake-writer`. Use whenever the user asks "review this renewal," "what should we do about X's renewal," "remarket Y," or when `revenue-sentinel` flags a 90/60/30-day checkpoint.
---

# Renewal Review

The Project 85 triage skill. Turns "renewal coming up" into a structured
review with a recommendation, a risk classification, and a logged action.

## When to use

- User says "review the renewal for X" or "what should we do about Y's
  renewal?"
- `revenue-sentinel` flags a Project 85 checkpoint (90/60/30 days).
- A renewal letter or rate-change PDF lands and needs assessment.
- A client expresses dissatisfaction with renewal pricing.
- A renewal stage advance is being considered (Identified → Outreach
  Sent → Quote Requested → Proposal Sent → Negotiating → Renewed-Won |
  Lost).

Do **not** use this skill for:

- Brand-new prospects → `commercial-risk-intake` /
  `personal-lines-intake`.
- Pure fact lookups → `crm-fact-retriever`.
- Drafting the remarket submission packet → `proposal-builder`.

## Inputs

```json
{
  "renewal_ref": {
    "policy_number": "POL-12345",
    "account_name": "JB Noble Construction LLC",
    "line_of_business": "Workers Compensation",
    "expiration_date": "2027-01-01"
  },
  "source_signal": "revenue_sentinel_30d | manual_request | renewal_letter | client_complaint | producer_request",
  "submitted_by": "Lamar Coates"
}
```

## Workflow

1. **Pull context** (use `crm-fact-retriever` semantics — but you may
   gather the full bundle, not just one fact):
   - EspoCRM Policy: `policy_number`, `carrier`, `effective_date`,
     `expiration_date`, `premium`, `line_of_business`, `status`,
     `amsLockState`.
   - EspoCRM Renewal: current `stage`, `current_premium`,
     `urgency`, `risk_status`.
   - EspoCRM Account: `account_status`, `annual_premium`,
     `last_contact_*`, `referral_source`.
   - Supabase `project_85_renewals` row (if it exists):
     `premium_current`, `premium_renewal`, `increase_percentage`,
     `risk_status`, `ai_strategy_notes`, `last_contact_date`.
   - `renewal_actions` history.
   - Loss runs / claims notes if attached.

2. **Compute renewal math**

   ```
   increase_pct = (premium_renewal - premium_current) / premium_current * 100
   premium_delta = premium_renewal - premium_current
   ```

   Don't compute when `premium_renewal` is unknown — set
   `renewal_indication: "not_yet_received"` instead.

3. **Classify risk** (use the `renewal_risk_status` enum):

   | risk_status | Trigger |
   |-------------|---------|
   | `SAFE` | Increase < 5% AND no loss activity AND client unprompted |
   | `AT_RISK` | Increase 5–15%, OR client expressed shopping intent, OR last contact > 60d |
   | `CRITICAL` | Increase > 15%, OR loss runs adverse, OR client said "shopping it", OR < 30 days to x-date with no outreach logged |
   | `RENEWED` | Worker confirmed `Renewed - Won` in CRM |
   | `LAPSED` | Past x-date without binding |

4. **Recommend an action**

   Pick exactly one from the allowed-action set, with rationale:

   - `RETAIN_AS_IS` — accept renewal, no remarket. Notify client; advance
     Renewal stage to `Renewed - Won` once bound.
   - `RETAIN_WITH_NEGOTIATION` — push carrier for credit / class change /
     payroll adjustment / loss-control credit; remain with current
     carrier.
   - `REMARKET_SAMPLE` — quote 1–2 alternates as comparison only.
   - `REMARKET_FULL` — full submission to 4+ markets via
     `proposal-builder`.
   - `ESCALATE_HUMAN` — non-deterministic situation; route to Slack
     `#rsg-hermes-project85-renewals`.
   - `MOVE_TO_AT_RISK_LIST` — client is shopping aggressively; needs
     producer-led save play.

5. **Stage advance** — propose the next Renewal stage, never skipping:
   `Identified → Outreach Sent → Quote Requested → Proposal Sent →
   Negotiating → Renewed - Won | Lost`. Moving backwards requires
   `requires_explicit_confirmation: true`.

## Output shape

```json
{
  "action": "renewal_review",
  "approval_required": true,
  "renewal": {
    "policy_number": "POL-12345",
    "account_name": "JB Noble Construction LLC",
    "carrier": "Travelers",
    "line_of_business": "Workers Compensation",
    "current_premium": 18420,
    "renewal_premium": 22810,
    "increase_pct": 23.8,
    "premium_delta": 4390,
    "days_to_expiration": 47,
    "current_stage": "Identified",
    "proposed_next_stage": "Quote Requested",
    "amsLockState": "Synced"
  },
  "context": {
    "loss_runs_3y": {"claims": 1, "incurred": 6200, "open": 0},
    "last_contact": {"date": "2026-10-12", "type": "Email", "outcome": "Reached"},
    "account_status": "Renewing",
    "whale_account": true,
    "cross_sell_open": ["Cyber"]
  },
  "risk_classification": {
    "risk_status": "CRITICAL",
    "reasons": [
      "Renewal increase 23.8% exceeds 15% threshold",
      "WC class code 5403 — carrier appetite tightening",
      "Days to expiration < 60 and no quote submitted yet"
    ]
  },
  "recommendation": {
    "action": "REMARKET_FULL",
    "rationale": "Carrier increase exceeds AT_RISK threshold; loss profile is favorable; account size justifies full remarket. Target 4 WC carriers with appetite for construction class codes.",
    "target_carriers_for_proposal_builder": ["AmTrust", "Berkshire Hathaway GUARD", "Liberty Mutual", "EMPLOYERS"],
    "client_message_draft_id": "renewal-msg-<ulid>"
  },
  "renewal_actions_draft": [
    {
      "renewal_id_or_policy_number": "POL-12345",
      "action_type": "remarket_full_submission",
      "due_date": "2026-12-15",
      "owner": "Lamar Coates",
      "notes": "Submit to 4 WC carriers. Use proposal-builder for packet."
    },
    {
      "renewal_id_or_policy_number": "POL-12345",
      "action_type": "client_outreach",
      "due_date": "2026-12-05",
      "owner": "Lamar Coates",
      "channel": "Phone",
      "notes": "Acknowledge increase, set expectation on remarket timeline, request loss-control updates."
    }
  ],
  "stage_transition": {
    "from": "Identified",
    "to": "Quote Requested",
    "valid": true
  },
  "cross_sell_opportunity_draft": null,
  "slack_escalation": {
    "channel": "#rsg-hermes-project85-renewals",
    "post_when_approved": true,
    "summary": "CRITICAL: JB Noble WC renewal +23.8% — REMARKET_FULL recommended; due 12/15."
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

## Hard rules

1. **`renewal_risk_status` enum only** — `SAFE`, `AT_RISK`, `CRITICAL`,
   `RENEWED`, `LAPSED`. No free text.
2. **No skipping renewal stages.** Forward only unless
   `requires_explicit_confirmation: true`.
3. **AMS lock awareness** — if `amsLockState = "Synced"` on the source
   policy, surface in the response and mark any field-change proposal
   as `requires_human_review`.
4. **No invented loss data.** Pull from CRM ActivityLog / loss runs /
   claims notes. If absent, mark as `unknown`.
5. **Renewal-only opportunity** — when a Cross-Sell shows up (e.g. Cyber
   for a tech client), hand off to `crm-intake-writer` for a NEW
   Opportunity rather than retrofitting the Renewal row.
6. **Slack escalation honors registry** — only post to
   `#rsg-hermes-project85-renewals` unless `slack_registry` says
   otherwise. See `espocrm-developer` and
   `hermes/operations/slack_router.py`.
7. **`renewal_actions` rows are append-only.** This skill drafts new rows;
   it never edits or deletes prior actions.
8. **Whale accounts get a human gate.** When
   `context.whale_account = true` and recommendation is
   `RETAIN_AS_IS`, change to `RETAIN_WITH_NEGOTIATION` minimum and add
   producer-call action.

## References

- `docs/agency-memory-plan.md`
- `docs/hermes-operating-constitution.md` — Project 85 contract
- `docs/revenue-sentinel.md` — checkpoint cadence
- `hermes-training/espocrm/workflows.md` — renewal stage enum
- `hermes-training/espocrm/guardrails.md` — risk status enum
- `crm-fact-retriever` — context gathering
- `crm-intake-writer` — cross-sell Opportunity drafting
- `proposal-builder` — remarket submission packet
- `revenue-sentinel` — upstream trigger

## Save to the document library

After the renewal review is written, persist it so it shows in **Agent OS →
Documents** (under the client's folder) + Holographic Memory:

```bash
hermes --doc-add \
  --doc-title "<client> — <policy/LOB> Renewal Review" \
  --doc-account "<EspoCRM account name>" \
  --doc-type renewal \
  --doc-file <path>          # or pipe the review via stdin
```

Or POST `/api/documents/save`: `{ "title", "content", "account_name",
"doc_type": "renewal", "source": "renewal-review" }`.
