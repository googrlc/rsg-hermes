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

## Read this before you report a number

### There is exactly one snapshot, and it is a manual baseline

`agency_snapshots` holds **one row**:

| Field | Value |
|---|---|
| `snapshot_date` | 2026-03-31 |
| `active_premium` | $385,000 |
| `policy_count` | 104 |
| `client_count` | 81 |
| `retention_rate` | **54.92** |
| `source` | `manual` |

The famous 54.92% is that hand-entered baseline, now ~4 months old. **No
week-over-week delta is possible.** Do not compute one, do not imply movement,
and do not present 54.92% as a current measurement. Say "baseline 2026-03-31;
no newer snapshot."

**Writing a fresh snapshot is the single highest-value action this skill can
take.** Until it happens, every run reports the same stale retention figure.

### The book number is unreliable by roughly $378K

Computed live from `canonical_policies` on 2026-07-26:

| Measure | Value |
|---|---|
| Active policies | 163 |
| Active premium | **$733,213** |
| Clients | 415 |
| **Tombstoned policies** | **48, carrying $378,575** |

Those 48 rows have a literal status of `Inactive: not in NowCerts 2026-07-21`
(43) or `...2026-07-23` (5). They were written by the `rsg-import` pg_cron path,
which pulled `is_quote=false` only and marked everything it didn't see as gone.
It was **disabled 2026-07-24** pending a single-writer fix.

Crucially they are marked `active=false`, so they are **excluded** from the
$733,213. The contamination therefore **suppresses** the book rather than
inflating it. If those tombstones are false, real active premium is up to
**$1.11M**. If they're genuine, $733K stands.

**Every scorecard must carry that band.** Reporting $733,213 as a clean number
is the failure mode.

Two smaller inconsistencies: 5 rows are `status='Expired'` with `active=true`,
and 2 are `'Renewed'` with `active=true`.

---

## Sources

| Card | Source |
|---|---|
| Premium / policies / clients | `canonical_policies`, `canonical_clients` via `hermes/ams/book.py` |
| Retention | `agency_snapshots` (latest) — currently the single baseline |
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
• 54.92% (baseline 2026-03-31, manual) → target 75% | gap 20.1 pts
• No newer snapshot — no trend available

💰 PIPELINE
• {n} opportunities, ${value} — {n} CRM-worked, {n} mirrored from the AMS

⚠️ RENEWAL RADAR
• 🔴 CRITICAL {n} (${prem}) • 🟡 AT_RISK {n} (${prem}) • 🟢 SAFE {n} (${prem})

🧾 DATA NOTE
• 48 policies ($378,575) tombstoned by the disabled import path and excluded
  from active premium. Real book is $733K–$1.11M until that's resolved.

🎯 GATE 1 ($425K premium / 60% retention)
• Premium: cleared on the reported figure • Retention: 54.92% → 5.1 pts short
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

- **No trend, and no automated snapshot writer.** One manual row, four months
  old; nothing in the scheduler writes `agency_snapshots`.
- **Retention is not computed anywhere.** 54.92% is typed in, not derived. Until
  a real calculation exists, every retention statement quotes that baseline.
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
