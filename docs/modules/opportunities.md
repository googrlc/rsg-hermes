# EspoCRM Opportunity — Renewal Retain Layer

**Contract document.** The Walker API writes renewal intelligence to the native
EspoCRM `Opportunity` entity via these 8 custom fields. This file is the
source of truth for the field names, types, and semantics — update here, then
update the EspoCRM entity manager.

## Custom Fields (8)

| Field (API name)   | Type        | Purpose                                                        |
|--------------------|-------------|----------------------------------------------------------------|
| `cRenewalSegment`  | enum        | Cadence segment: auto_6mo, personal_12mo, commercial_small, commercial_mid, benefits |
| `cRenewalOwner`    | varchar     | Who owns this renewal: Lamar or Gretchen                        |
| `cComplexityFlags` | text        | Pipe-delimited flags, e.g. "GL open since 2025-10-04"          |
| `cRenewalDecision` | enum        | Terminal outcome: renewed, rewritten, lost_price, lost_coverage, lost_no_response, do_not_renew |
| `cTouchLog`        | text        | JSON array of touch entries: [{timestamp, actor, channel, note}] |
| `cHandoffNotes`    | text        | Free-text notes for handoff between Lamar and Gretchen         |
| `cDay1SentAt`      | datetime    | When the Day-1 renewal email was sent                          |
| `cLastClientContactDate` | date  | Last contact with client (updated on every touch)              |

## Deliberately Skipped (2)

| Field (NOT added)   | Reason                                                        |
|---------------------|---------------------------------------------------------------|
| `cNextTouchCode`    | Timer artifact — nothing fires on a clock in the Walker model |
| `cNextTouchDate`    | Timer artifact — follow-up awareness is computed at request time, not scheduled |

## Write Semantics

- **POST /walker/touch** — appends to `cTouchLog`, updates `cLastClientContactDate`
- **PATCH /walker/worksheet** — updates arbitrary `c*` fields + `stage`/`amount`
- **POST /walker/flag** — appends to `cComplexityFlags` (pipe-delimited, idempotent)
- **POST /walker/handoff** — overwrites `cHandoffNotes`
- **POST /walker/outcome** — sets `cRenewalDecision` + optionally `stage`

## Follow-Up Awareness (GPT Instruction)

When a snapshot shows Day-1 sent (`cDay1SentAt` populated) with no logged
response in `cTouchLog`, the GPT computes days elapsed and surfaces the overdue
follow-up with the draft. Hermes does NOT schedule this — the GPT notices it
at request time and Lamar decides whether to act.

## Enum Values

### cRenewalSegment
- `auto_6mo` — 6-month personal auto
- `personal_12mo` — 12-month personal lines
- `commercial_small` — commercial, account premium <= $5,000
- `commercial_mid` — commercial, account premium > $5,000
- `benefits` — group benefits / PEO

### cRenewalDecision
- `renewed` — retained at current carrier
- `rewritten` — moved to new carrier
- `lost_price` — lost due to price
- `lost_coverage` — lost due to coverage terms
- `lost_no_response` — lost, client went silent
- `do_not_renew` — RSG or carrier chose not to renew
