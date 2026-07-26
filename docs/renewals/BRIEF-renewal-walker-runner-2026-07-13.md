# BRIEF — Renewal Walker × Hermes Runner (v3)

**Date:** 2026-07-13 (v3 supersedes v2 same day)
**Approver:** Lamar
**Builder:** Hermes (`rsg-hermes` repo → `/opt/rsg-hermes` on `rrespocrm-rsg`, git-first deploy)
**Companion docs:** `BRIEF-renewal-cadence-2026-07-09.md` · RSG Renewal Walker GPT Setup Kit v2 · RSG Renewal Worksheet template
**Priority:** Revenue-critical. This is the 54.92% → 80% retention machine.

> **⚠️ HISTORICAL — this design is retired (2026-07-24).** Read it for the cadence,
> segment, and classification reasoning, which still hold. Do not build from it.
>
> - **The execution path was superseded 2026-07-15** by the **Hermes Renewal Executor
>   — Job Contract v2**: a queue-driven worker (`hermes/renewals/executor.py`) that
>   executes only human-approved instructions staged in `outbound_sync_queue` and
>   writes receipts to `renewal_execution_receipts`.
> - **The read + draft path was retired 2026-07-24.** The `/walker/*` API kept its
>   whole retain layer in EspoCRM `Opportunity` custom fields; with EspoCRM removed as
>   a data source the service was deleted rather than rebuilt. Renewals are worked in
>   the cockpit.
>
> See [`README.md`](./README.md) for what actually runs.

**What changed in v3:** ChatGPT Business is the agency workstation — everything sits on it. So the paste-block bridge dies, the Walker GPT gets an **Action wired to the Hermes bridge**, the worksheet stops being a file anywhere (Drive and canvas are both out — it's CRM state rendered by the Walker on demand), and Slack is demoted to doorbell + siren.

---

## Mission

One system, four roles:

- **Renewal Walker GPT (ChatGPT Business) = the workstation.** Gretchen AND Lamar work renewals here. It pulls the queue from Hermes, walks one step at a time, renders and patches the worksheet, and posts every outcome back. It never invents data — it only knows what the Hermes API returns.
- **Hermes = the runner and the API.** Nightly classify → compute due touches → serve the Walker API → write every touch, flag, handoff, and outcome to the CRM/NowCerts → fire doorbells and escalations → compute the scoreboard. Hermes never talks to a client.
- **Slack = the notification wire only.** Morning doorbell DM to Gretchen ("3 renewals due — open the Walker"), 🚨 escalations and 📋 handoffs to `#lamar-alerts`, Monday digest. No data-bearing cards, no paste blocks, no reply grammar as the primary path.
- **Gretchen = the only hands that touch clients.** The Walker drafts; she sends.

**Why this beats v2:** business rules ($5K cutoff, 10% remarket threshold, cadence timing) now live in exactly ONE place — Hermes config. The GPT carries zero numbers, so the "make sure the GPT and the Hermes module agree" drift problem is dead by design. And the 7/12 decision survives intact: ChatGPT still never touches NowCerts. It talks only to Hermes; Hermes owns every write.

---

## Standing Rules (carried forward — do not relitigate)

1. **No new agents.** Module extension of `hermes/renewals/` (7 files, 14 tests).
2. **No new Supabase tables.** Source = `canonical_policies`. State = fields on the CRM renewal Opportunity. Hermes re-derives due work from CRM state every morning.
3. **Draft-and-approve.** Hermes and the Walker draft; a human sends.
4. **Gretchen's pings are DMs, never channel posts.** Escalations/handoffs → `#lamar-alerts` (ID resolved by name at startup, fail loud).
5. **Touch timing keys to `expiration_date`** in `canonical_policies`.
6. **Medicare excluded from all automated client touches.** T-65 watcher stays internal-only, separate brief. Never age-reference a client in writing.
7. **Git-first deploy.** No live edits on the VPS. `hermes` account keeps `nologin`.
8. **NEW — The GPT talks to Hermes only.** Never NowCerts direct, never the CRM direct. One Action, one scoped API key, one owner of writes.

---

## The Loop (one renewal, end to end)

1. **Nightly:** Account Sync v2 (2:00a) + Policy Sync v2 (2:30a) refresh `canonical_policies`. Dedup at the ingest boundary.
2. **7:30a ET, Hermes scan:** classify segment (account-level totals), classify owner, auto-detect complexity flags, compute today's due touches, write state to the renewal Opportunities.
3. **7:35a doorbell:** Slack DM to Gretchen — one line, no client data: "☀️ 3 renewals due today (2 yours, 1 handoff prep). Open the Renewal Walker → type queue."
4. **Gretchen opens the Walker, types `queue`** → Action `GET /walker/queue` → today's list. She picks one: `work {client}`.
5. **Walker pulls the snapshot** (`GET /walker/renewal/{id}`), runs the data quality check, and walks her one task card at a time. She sends every client message herself.
6. **Touch done →** Walker confirms, then `POST /touch`. Hermes stamps `cDay1SentAt`, arms the D4/D7/D14 ladder, advances stage to **Renewal Notice Sent**, logs to NowCerts + the CRM.
7. **Client responds →** she tells the Walker; it posts the outcome and stops the ladder. **Client silent →** doorbell at D4 and D7; at D14 Hermes fires 🚨 to `#lamar-alerts`.
8. **Complexity appears** (upset client, exposure change, gap, remarket) → she tells the Walker → `POST /flag` → owner flips to Lamar → Walker switches to prep-and-push: finish worksheet, send first questionnaire touch, then `handoff` → `POST /handoff` → Hermes posts 📋 to `#lamar-alerts` AND the renewal lands in Lamar's Walker queue.
9. **Lamar types `handoffs` in the same Walker** → his prepped queue, worksheets rendered inline. He works it; outcomes post the same way.
10. **Outcome →** `POST /outcome` → stage to **Bound/Renewed** or **Non-Renewal/Lost**, `writtenPremium`/`lostReason` written, +7 thank-you/review card scheduled.
11. **Monday 8:00a:** digest DM to Lamar. Same numbers live in the Walker via `scoreboard`.

---

## Walker API (rides the existing Hermes HTTPS bridge — nginx, API-key auth, rotation as practiced)

| Endpoint | Does |
|---|---|
| `GET /walker/queue?owner=` | Today's due renewals for Gretchen or Lamar |
| `GET /walker/renewal/{id}` | Full snapshot: policy data, segment, owner, flags, worksheet state, touch history |
| `POST /walker/renewal/{id}/touch` | `{code, channel, outcome}` — stamps timestamps, arms timers, advances stage |
| `PATCH /walker/renewal/{id}/worksheet` | Structured worksheet field updates |
| `POST /walker/renewal/{id}/flag` | `{reason}` — appends flag, flips owner to Lamar |
| `POST /walker/renewal/{id}/handoff` | `{done, needs, expects_by}` — 📋 card + Lamar's queue |
| `POST /walker/renewal/{id}/outcome` | `{result, final_premium, reason}` — closes the cycle |
| `GET /walker/scoreboard` | Rolling retention %, week's renewed/lost premium, pipeline counts |

**Auth:** new API key scoped to `/walker/*` only — not full Hermes admin. Lives in the GPT's Action config (auth type: API key, custom header). Rotate on the existing schedule.

**GPT Actions setup (add to the kit's install steps):** Configure tab → Actions → paste the OpenAPI schema (Hermes serves it at `/walker/openapi.json` — generate from FastAPI/Flask route defs) → auth = API key → set GET endpoints to always-allow, POST/PATCH to ask-once. Browsing/images/code-interpreter stay OFF; Actions are configured separately and unaffected.

---

## Triage Classifier (unchanged from v2 except flag delivery)

Segments and cadence per the 7/9 brief: 6-mo auto T-30 · personal 12-mo T-45/T-15 · small commercial (<$5K account) T-40/T-15 · mid/large ($5K+) and benefits T-90/T-60/T-30 + T-15 confirm. Owner defaults Gretchen; flips to Lamar on mid/large, benefits, or any complexity flag.

*Hermes auto-detects:* open claim · renewal premium change ≥ threshold (via overnight change-capture) · payment lapse if derivable · carrier non-renewal notice when captured.
*Gretchen reports via the Walker* (`flag` → `POST /flag`): client upset/leaving/attorney · coverage gap · exposure/operations change · remarket needed.

Stop-and-escalate class (claim interpretation, attorney, cancellation threat) → Walker halts client contact AND Hermes fires 🚨 immediately.

---

## The Worksheet Is Not a File

Canonical worksheet = structured state on the renewal Opportunity. The Walker renders it on `worksheet`, populates it on `fill worksheet` (then PATCHes), and shows it inline in handoff view. Nobody files anything, nothing drifts, and the CRM record is complete the moment the renewal closes. PDF export for an E&O file or client-facing copy → parking lot, one endpoint away if ever needed.

**Proposed Opportunity field additions** (Entity Manager → then update `modules/opportunities.md` in the field reference and commit — the contract rule):

| Label | Field Name | Type | Values |
|---|---|---|---|
| Renewal Segment | cRenewalSegment | Enum | 6-Mo Auto, Personal 12-Mo, Small Commercial, Mid/Large Commercial, Group Benefits |
| Renewal Owner | cRenewalOwner | Enum | Gretchen, Lamar |
| Complexity Flags | cComplexityFlags | Multi-Enum | Open Claim, Non-Renewal Notice, Remarket Needed, Coverage Gap, Payment Lapse, Client At-Risk, Exposure Change |
| Renewal Decision | cRenewalDecision | Enum | Renew As-Is, Remarket, Handoff |
| Touch Log | cTouchLog | Text | append-only: `2026-07-20 T-30 email sent — done` |
| Handoff Notes | cHandoffNotes | Text | done so far / needs from Lamar / client expects by |
| Day-1 Sent At | cDay1SentAt | Date-Time | — |
| Next Touch | cNextTouchCode | Varchar | T90/T60/T45/T40/T30/D4/D7/D14/T15/POST7 |
| Next Touch Date | cNextTouchDate | Date | — |
| Last Client Contact | cLastClientContactDate | Date | verify — may exist from June retention-scan prereq |

Existing fields used as-is: `stage`, `businessType=Renewal`, `cRenewalDate`, `amount`, `estimatedPremium`, `writtenPremium`, `currentCarrier`, `lastContactMethod`, `lostReason`, `aiSummary`, `assignedUser`, `cClientEmail`.

---

## GPT v3 — Replace the Intake/Worksheet Blocks With This

```
# ACTION MODE (Hermes is your data source — the ONLY one)
You are connected to RSG's Hermes service via Actions. You know NOTHING about any
renewal except what the API returns. Never invent, estimate, or fill in policy data.

SHORTCUTS:
- "queue" → GET the user's due renewals; list as numbered cards, ask which to work
- "work {client}" → GET the renewal; run the data quality check on what came back
  (🔴 blockers / 🟡 fix-while-here); then task cards ONE AT A TIME
- "worksheet" → render the worksheet from API state; mark unknowns [NEED]
- "fill worksheet" → collect the missing fields conversationally, then PATCH
- "flag {reason}" → POST the flag; owner flips to Lamar; switch to prep-and-push
- "handoff" → collect done-so-far / needs-from-Lamar / client-expects-by; POST it
- "handoffs" (Lamar) → GET his queue; render each with worksheet inline
- "status" → GET the renewal; one-line state + single next action
- "scoreboard" → GET and show retention numbers, no commentary

RULES:
- Confirm with the user BEFORE any POST or PATCH that changes state. Reads are free.
- You draft client messages; Gretchen sends them. Never imply anything was sent.
- A touch is only "done" after she says she sent it — then POST it.
- If an Action call fails, say so plainly and give the manual fallback: reply in the
  Hermes Slack thread using: done · log: <text> · flag: <reason> · handoff ·
  renewed $<amt> · lost <reason> · pending.
- All existing guardrails stand: no coverage interpretation on live claims, no
  binding, PII redaction, stop-and-escalate triggers.
```

Everything else in the v2 instructions (task card format, cadence behavior descriptions, communication voice, hard guardrails, worksheet layout) stays.

---

## Fallback Mode (cheap insurance, keep it)

The v2 Slack thread-reply grammar (`done` / `log:` / `flag:` / `handoff` / `renewed $` / `lost` / `pending`) stays implemented in Hermes behind a feature flag. If OpenAI or the Action is down, Hermes flips to data-bearing cards with paste blocks and Gretchen works the day the v2 way. The workstation has a backup door.

---

## Phases & Gates

**Phase 0 — unchanged from 7/9, still gates everything:**
1. Commit, push, deploy the pending Hermes patch.
2. Resume `rsg-sync-daily`; Account Sync v2 + Policy Sync v2 running clean.
3. Stale `canonical_policies` statuses resolved.
4. Dedup at ingest: Shamira Douglas pair, Exquisite Delites.
5. **Manual fires:** Perez (date passed), Gray 7/20, Nubian Clean 7/24, Richards 7/26 — confirm each got a human touch.
**Hard gate stands: a proactive touch on a stale renewal date is worse than silence.**

**Phase 1 — Walker API + shadow (build week 1):** classifier + owner + auto-flags · the CRM field additions · `/walker/*` endpoints + scoped key · Action wired into the GPT · **Lamar is the only user for 5 business days** — he works his own queue in the Walker daily. Gate: zero wrong dates, zero ghost renewals, segment/owner correct on every card, every POST lands in the CRM.

**Phase 2 — Gretchen live (week 2):** doorbell DMs on · escalations + D-ladder + T-15 confirm · Monday digest · 30-minute walkthrough with one real renewal · GPT shared to the workspace.

**Phase 3:** draft-and-approve client email generation (7/9 brief scope) · +7 thank-you/review · cross-sell flag surfacing (bundle gap, life mention, benefits 5+; T-65 stays internal).

**Parking lot:** Block Kit buttons · portal questionnaire automation · worksheet PDF export · client-facing portal.

---

## Decisions Table

| Decision | Value | Status |
|---|---|---|
| Small/mid commercial cutoff | $5,000, account level | Locked 7/9 |
| Escalation destination | `#lamar-alerts` | Locked 7/9 |
| Bridge architecture | GPT Action → Hermes `/walker/*`; ChatGPT never touches NowCerts | Locked 7/13 (this brief) |
| Personal-lines remarket threshold | 10% increase | Default — confirm |
| Shadow mode length | 5 business days, Lamar-only | Default |
| Daily scan / doorbell time | 7:30a / 7:35a ET | Default |
| Action confirmation policy | GETs always-allow, POSTs ask-once | Default |
| Walker API key scope | `/walker/*` only | Default |
| Gretchen's Slack member ID | — | **Needed before Phase 2 (doorbell)** |
| Gretchen's phone in GPT sign-off | — | **Needed — kit edit item 3** |
| Questionnaire portal URL | — | **Needed — kit edit item 4** |

---

## Risk Register (one line each, eyes open)

- **Pull-based workstation:** if nobody opens the Walker, nothing happens → the doorbell DM is not optional; it's the safety net.
- **OpenAI outage:** fallback grammar flag flips the system to v2 paste cards; the day still runs.
- **Data transit:** NowCerts client data flows through OpenAI via Action responses — same exposure class as pasting it in, Business workspace (no training on business data), and the PII guardrail (no SSN/DL/bank) still stands.

---

## Retention Scoreboard

Rolling 12-month premium retention = Σ `writtenPremium` (Bound/Renewed) ÷ Σ expiring premium (Bound/Renewed + Non-Renewal/Lost), trailing 12 months. In every Monday digest and behind the Walker's `scoreboard` command: **"Retention: 61.3% (baseline 54.9%)"** — the number stares back weekly, moving or not.

#sop
