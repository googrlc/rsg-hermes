---
name: book-health-monitor
description: >
  Weekly agency book health scorecard for RSG — premium, policies, clients,
  retention, pipeline, and the renewal radar, assembled from the canonical
  Supabase book (`canonical_policies` / `canonical_clients`), the `opportunities`
  pipeline, and `agency_snapshots`. Boss-level only; Gretchen does not receive it.
  Triggers on "book health", "book check", "how's the book", "agency scorecard",
  "weekly scorecard", or the Monday 10am ET schedule. Revenue-critical.
---

# Book Health Monitor

Lamar should never have to wonder how the agency is doing.

> **Rewritten 2026-07-26.** The prior version minted a NowCerts token from
> `op://claudeclaw/NowCerts/*`, pulled pipeline from EspoCRM, and instructed a
> week-over-week delta against "the prior snapshot". EspoCRM is retired, those
> vault paths are stale, and **there is no prior snapshot** — see below.

---

## The snapshot is now computed, not typed

**`hermes --agency-snapshot` writes one `agency_snapshots` row per day** (cron
5:45am ET), built by
[hermes/jobs/agency_snapshot.py](../../../hermes/jobs/agency_snapshot.py). Run
it — don't hand-assemble the numbers, and don't re-derive retention.

Preview with zero writes: `hermes --agency-snapshot-dry-run`.

Before this existed, `agency_snapshots` held a single hand-typed baseline
(2026-03-31, $385,000, 104 policies, 81 clients, retention **54.92%**,
`source='manual'`) and nothing ever wrote a second one. That is where the
much-quoted 54.92% came from. **It is a four-month-old manual entry, not a
measurement** — treat any older report citing it accordingly.

### Live numbers (verified 2026-07-26, live AMS)

| Measure | Value |
|---|---|
| Active premium | **$738,919** |
| Active policies | 163 |
| Clients | 415 |
| Open pipeline | $257,628 across 55 |
| **Retention (premium-weighted, trailing 12mo)** | **60.78%** |
| Retention (logo) | 65.34% (115/176 terms) |

**Retention is 60.78%, not 54.92%** — up 5.9 points on the baseline. Nobody knew
because nothing was computing it.

Note logo (65.34%) runs *above* premium-weighted (60.78%): the agency is keeping
more policies than dollars, i.e. **the churn is concentrated in larger accounts.**
That gap is worth a sentence in any scorecard.

### Read live, not the mirror

The numbers above come from the AMS with `HERMES_AMS_LIVE_READS` set. Off that
flag, the job falls back to `canonical_policies`, which carries ~178 stale rows
and **overstates retention by about 6 points** (66.66% vs 60.78%) by creating
phantom "later term" matches. The snapshot's `notes` field always records which
source produced it — check it before quoting a number.

The 48 rows tombstoned `Inactive: not in NowCerts 2026-07-21/23` by the disabled
`rsg-import` path were checked against the live AMS on 2026-07-26: **28 still
exist in NowCerts (24 of them active, $246,027) and 20 are genuinely gone.** So
the tombstones were roughly half wrong — but the live read already resolves it,
which is why $738,919 is a clean figure and the old "±$378K" band no longer
applies. Tombstoned terms are excluded from the retention denominator so the
importer bug can't manufacture churn.

---

## Sources

| Card | Source |
|---|---|
| Premium / policies / clients | `canonical_policies`, `canonical_clients` via `hermes/ams/book.py` |
| Retention | `agency_snapshots` (latest row, written daily by `--agency-snapshot`) |
| Pipeline | `opportunities` (63 rows) |
| Renewal radar | `renewal_candidates` / `project_85_renewals` |
| Approval queue | `cc_submissions` where `status='in_review'` |

`hermes/command_center/dashboard.py::kpi_summary` already assembles most of
this — prefer calling it over re-deriving. Note its `pipeline` field returns
`None`; that card is not wired.

Premium per policy resolves in order: `annualized_premium` →
`current_term_amount` → `premium_amount`.

### Live pipeline and renewal standing (2026-07-26)

- **Pipeline:** 63 opportunities — but 62 arrived from the AMS syncs
  (`nowcerts_quote_sync` 50, `nowcerts-opportunity-sync` 12) and only 1 is
  CRM-worked. Report it as a mirror, not an actively-managed pipeline.
- **Renewal radar:** 48 in the working queue — 10 `CRITICAL` ($30,727),
  25 `AT_RISK` ($64,137), 13 `SAFE` ($76,926). ~$94.9K at risk.

---

## Scorecard format

Lead with the number and the decision. Plain English.

```text
📋 RSG BOOK HEALTH — {day} {date}

📊 THE BOOK
• Active premium: ${active_premium}  (±$378K — see data note)
• Policies: {active}/{total} active   • Clients: {clients}

🔄 RETENTION
• {retention_rate}% premium-weighted, trailing 12mo → target 75%
• Logo {logo_rate}% — gap vs premium-weighted means churn is in the bigger accounts
• vs prior snapshot: {delta_retention:+.2f} pts

💰 PIPELINE
• {n} opportunities, ${value} — {n} CRM-worked, {n} mirrored from the AMS

⚠️ RENEWAL RADAR
• 🔴 CRITICAL {n} (${prem}) • 🟡 AT_RISK {n} (${prem}) • 🟢 SAFE {n} (${prem})

🧾 SOURCE
• {live AMS | canonical_policies mirror} — from the snapshot's `notes` field.
  Mirror-sourced numbers overstate retention ~6 pts; say so if that's the source.

🎯 GATE 1 ($425K premium / 60% retention)
• Premium: $738,919 → cleared • Retention: 60.78% → cleared (60% bar)
```

If any renewal is ≤14 days out, append the critical list: client, expiration,
premium, LOB.

---

## Gate progress

Gate 1 targets **$425,000 premium / 60% retention**.

- Both hit → "🎉 GATE 1 UNLOCKED"
- Retention only → "✅ Retention cleared — need $X more premium"
- Premium only → "✅ Premium cleared — need X.X pts more retention"
- Neither → "🔒 XX% premium / XX% retention"

The premium leg clears on the current reported figure — say so **with the ±$378K
band attached.** Clearing a gate on a contaminated number is exactly the mistake
worth avoiding.

---

## Error handling

| Situation | Action |
|---|---|
| Supabase unreachable | Report to `#systems-check`; no scorecard. |
| Snapshot write fails | Post the scorecard anyway; note "snapshot not saved". |
| No prior snapshot | **This is the current state.** Skip deltas; say so explicitly. |
| Mirror disagrees with NowCerts | NowCerts wins. Flag the drift. |

## Known gaps

- **The trend starts now.** The writer shipped 2026-07-26; before that there was
  one manual row. Expect a thin history for the first few weeks.
- ~~Retention is not computed anywhere.~~ **Fixed 2026-07-26** — computed from
  policy lineage by `hermes/jobs/agency_snapshot.py`, written daily.
- **The pipeline card is unwired** in `kpi_summary` (`pipeline: None`).
- **`dashboard_kpis`** exists with a `record_kpi` writer, but this scorecard
  doesn't feed it.

## Notes

- Schedule: Monday 10:00am ET, plus on demand.
- Boss-level only. **Gretchen does not receive this.**
- Prefer the canonical mirror over live AMS paging — and state the caveat rather
  than hiding it.

## References

- `hermes/command_center/dashboard.py` — `kpi_summary`, approval queue, feed
- `hermes/ams/book.py` — canonical book reads
- `hermes/operations/kpi_writer.py` — `record_kpi` → `dashboard_kpis`
- `retention-risk-scout` — the renewal radar detail
- `nowcerts-skill` — the mirror-vs-AMS contract
