# Gretchen-Lane Intake — E2E Sample Walkthrough + Accuracy Audit

**Date:** 2026-06-12 · **Run by:** Claude Code · **Target:** Hermes intake flow on the
gretch box (`hermes-gretch-u69864`, `/api/intake` → `hermes-intake-worker` →
LLM synthesizer → Slack draft → approval → CRM). **All test data fictional.**

> **Scope note (read first).** The original audit prompt assumes a pipeline driven by
> **Paperclip** with **ATTOM enrichment**, a **status page**, and `#gretchen-tasks`
> routing. That pipeline is **not what is deployed.** Per the owner's call, this audit
> tests **what actually runs**: Hermes owns intake end-to-end via its `intake_worker`
> (LLM extraction, no Paperclip). Hops that don't exist are reported as MISSING, not
> faked. See `docs/intake.md` for the real architecture.

---

## 1. VERDICT: ⚠️ CONDITIONAL PASS

**The brain passes; the last mile is broken.**

- ✅ **Extraction is quote-ready and fabrication-free.** On the Webb home+auto sample,
  every extractable field matched ground truth (17/17), the two LOBs were split into
  two un-bundled opportunities, both planted gaps were left un-invented, the
  note-only detail surfaced, and DOBs were correctly marked `restricted`.
  **Fabrication count: 0.** Gretchen could quote off the synthesized facts + opportunities.
- ❌ **Gretchen never receives the draft.** The Slack draft post fails with
  `channel_not_found` — the draft channel is misconfigured. This **blocks go-live**
  until fixed (one env var).
- ❌ Several audit-doc hops are **not built**: status-page endpoint, ATTOM enrichment,
  explicit lane auto-router, and a field-level "exception → #the-boss" path.

**FAIL conditions checked:** fabrication (0 ✅), planted gap guessed (no ✅), duplicate
created two rows (no — idempotent ✅), exception path silent (partial — see §5),
Summary unusable for quoting (no — usable ✅). The only hard failure is **notification
delivery**, which is config, not logic.

---

## 2. Hop-by-Hop Trace

| Hop | Result | Evidence |
|---|---|---|
| Envelope validation | ✅ PASS | `POST /api/intake` → `202`, `source` server-trusted, body validated by `IntakeSubmissionRequest`. Empty payload correctly rejected `422`. |
| Supabase queue | ✅ PASS | One row `ff62ef4c-…`, status `received`, payload stored intact. |
| Duplicate suppression | ✅ PASS | Identical re-POST → `200`, **same id**, `idempotent_replay:true`, **1 row** for the key. |
| Worker pickup | ✅ PASS | `Claimed intake submission ff62ef4c… (synthesizing)` → `received→synthesizing`. |
| Extraction (LLM synth) | ✅ PASS | `synthesizing→synthesized`; full `draft_summary` + `hermes_blocks` produced (see §4). |
| Routing gate | ⚠️ IMPLICIT | Classified `account_type=Personal Lines` (the gretchen-lane equivalent). **No explicit auto-router** in this flow — every draft goes to one channel; lane YAMLs are used by the *separate* command-center web flow, not `/api/intake`. |
| Enrichment (ATTOM) | ❌ MISSING | No ATTOM/property enrichment exists. |
| Summary generation | ✅ PASS | Quote-ready `hermes_blocks` (account + 2 contacts + 2 opps) + structured `draft_summary`. |
| **Slack notification** | ❌ **FAIL** | `SlackNotifierError: … channel D0B2PJYLGQG: {"error":"channel_not_found"}`. Draft never delivered. Row still settled `awaiting_approval` (non-fatal by design). |
| Status page | ❌ MISSING | `/api/intake/{id}/status` URL is returned but **no GET handler exists** (404). |
| CRM log / write | ⏸️ NOT EXERCISED | Deliberately did **not** click APPROVE — that enqueues real EspoCRM writes, which the audit's "no real record" rule forbids. The `approved→writing→complete` arc is unit-tested but not run here. |

System **failure alerts DO deliver** (backlog failures posted to `C0ANSEP6SSD`/systems-check fine) — only the **Gretchen-facing draft channel** is broken.

---

## 3. Accuracy Audit — Webb Home+Auto Sample

Submission `ff62ef4c-3a2b-4690-8369-070953b9a2ec` vs `ground_truth.json`.

| Field | Ground truth | Extracted | Result |
|---|---|---|---|
| Insured | Marcus Webb | Marcus Webb (primary contact) | ✅ match |
| Spouse | Sarah Webb | Sarah Webb (contact) | ✅ match |
| Account name | Webb Household | Webb Household | ✅ match |
| Address | 1487 Sandtown Road SW, Marietta GA 30064 | same | ✅ match |
| Home carrier | Travelers | Travelers (fact) | ✅ match |
| Home premium | $1,840 | $1,840 (fact) | ✅ match |
| Home expiration | 2026-08-15 | 2026-08-15 (fact) | ✅ match |
| Auto carrier | Progressive | Progressive (fact) | ✅ match |
| Auto premium | $2,150 | $2,150 (fact) | ✅ match |
| Driver 1 DOB | 1985-03-12 | 1985-03-12 (**restricted**) | ✅ match + correct sensitivity |
| Driver 2 DOB | 1987-07-22 | 1987-07-22 (**restricted**) | ✅ match + correct sensitivity |
| Requested lines | Homeowners + Personal Auto | 2 opportunities, **unbundled** | ✅ match (HARD RULE 1 ok) |
| Target effective | 2026-08-15 | both opps dated 2026-08-15 | ✅ match |
| **Note-only detail** (home office) | present | in `operations_summary` **and** note body | ✅ **not ignored** |
| **Planted gap — vehicle 2 VIN** | must be flagged, not guessed | note: *"VIN not provided"* | ✅ **flagged, not fabricated** |
| **Planted gap — roof year** | must be flagged, not guessed | absent (not invented) | ⚠️ **not fabricated, but not surfaced** as a needed field |

- **Extraction accuracy: 17/17 extractable fields = 100%.**
- **Fabrication count: 0.** (Scanned the whole draft for an invented RAV4 VIN and a roof year — neither present.)
- **Missing-flag score: 1.5 / 2** — VIN2 explicitly flagged; roof year silently dropped (not wrong, but Gretchen wouldn't be prompted to ask).
- Out-of-schema detail (VIN of vehicle 1, year built, sq ft, roof material) lands in the note narrative, not structured fields — expected for a CRM-intake draft (these belong in the quote app, not this object).

---

## 4. The Actual Summary (judge it yourself)

Rendered `hermes_blocks` posted for review:

```
Hermes:

MODULE: account
ACTION: upsert
  NAME: Webb Household
  ENTITY TYPE: Other
  ADDRESS: 1487 Sandtown Road SW
  CITY: Marietta
  STATE: GA
  ZIP: 30064
  ACCOUNT TYPE: Personal Lines
  ACCOUNT STATUS: Active
  OPERATIONS: Homeowners and personal auto insurance for Marcus Webb and spouse
              Sarah Webb. Detached garage converted to home office last year.

MODULE: contact  (Marcus Webb — Decision Maker, primary)
MODULE: contact  (Sarah Webb — Spouse)

MODULE: opportunity  Webb Household - Homeowners - 2026-08-15   (Discovery, New Business)
MODULE: opportunity  Webb Household - Personal Auto - 2026-08-15 (Discovery, New Business)
```

Note body (facts vs assumptions, source-cited) captured: both carriers + premiums,
both DOBs, both vehicles with *"VIN not provided"* on the RAV4, the home-office
conversion, and target effective date. **A producer could open markets from this.**

---

## 5. Exception / Incomplete Path

- **Truly empty payload** → rejected at the door with **`422`** (validator requires
  `transcript` or `documents`). Good fail-fast. ✅
- **Near-empty but valid** ("Jordan Blake / unreadable photo") → produced a **degenerate
  draft** (`Blake Household`, contact `Jordan Blake`, 0 opportunities, **no fabricated
  address/phone**) and went to `awaiting_approval`. ⚠️
- **Empty-payload rows from the email lane** (19 of them, pre-existing) → cleanly
  transitioned to `failed` with error logged + alert to systems-check. ✅

**Finding:** there is **no completeness gate**. Any text yields a draft; the pipeline
never raises a loud, field-level "too incomplete to quote → #the-boss" exception. The
only true exception is empty-payload (caught at the door). The good news: even on thin
input the synthesizer **does not fabricate** to fill gaps.

---

## 6. Defects (ranked)

**Blocks-Gretchen**
1. **Slack draft delivery broken** — `HERMES_INTAKE_DRAFT_CHANNEL` unset → falls back to
   `HERMES_SENTINEL_SLACK_CHANNEL` = `D0B2PJYLGQG` = `channel_not_found`. Gretchen never
   sees drafts. **Proposed fix:** set `HERMES_INTAKE_DRAFT_CHANNEL` to a valid channel
   (e.g. `#gretchen-tasks` `C0AMWAZBBJP` or her DM), invite the bot, re-run.
2. **No status-page endpoint** — `/api/intake/{id}/status` is advertised but 404s.
   **Proposed fix:** add the GET handler (reads the row's status/history).

**Fix-soon**
3. **Email-triage lane writes empty-payload intake rows** — 19 rows since ~May 31 with
   `transcript_len=0, ndocs=0`, all now `failed`. The email→intake payload mapping is
   dropping the body. (Separate from the Gretchen lane, but it's spamming systems-check.)
4. **No completeness gate** — incomplete submissions become thin drafts instead of a
   loud, field-level exception to #the-boss (see §5).
5. **Roof-year gap silently dropped** — not fabricated, but not surfaced for follow-up.

**Cosmetic**
6. Slack error text says *"Failed to post **sentinel briefing**"* for intake posts
   (hardcoded string in `slack_notifier.py`).
7. Worker logs `note='draft posted to Slack'` on the transition even when the post
   **failed** — misleading audit trail.

---

## 7. Cleanup

- Test rows created this run: `ff62ef4c…` (Webb), `e61d8025…` (incomplete),
  `8545714d…` (deploy-verify), plus the `422`-rejected empty (never inserted).
  **Purged** from `intake_submissions` after this report (none were approved → **no
  EspoCRM records created**, per the no-real-record rule).
- **Intake worker left running** as a compose service (`rsg-hermes-intake-worker`) — it
  was the missing piece (submissions previously stalled at `received`). This is the one
  intentional pipeline change made during the audit; committed as `af4664d`.
- No `[TEST]` Slack posts to leave for review — they never delivered (defect #1).

---

## 8. Bottom line

The extraction engine — the part that decides whether Gretchen can quote — **works and
does not lie.** Fix the one env var (draft channel) and add the status endpoint, and the
Gretchen lane is ready to pilot. No Paperclip, ATTOM, or new architecture required to
get her selling.
