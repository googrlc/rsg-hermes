---
name: commission-reconciliation
description: >
  Commission reconciliation for RSG — expected vs actual commission, delta
  detection, and what the agency is still owed, against the Supabase money
  tables (`commission_ledger`, `commission_transactions`, `commission_rules`,
  `commission_reconciliation`) and the `v_reconciliation_*` views. Use for
  "reconcile commissions", "what are we owed", "commission deltas", or after a
  carrier statement is ingested. Money data never auto-commits.
---

# Commission Reconciliation

Tracks what RSG earned against what carriers actually paid.

> **Rewritten 2026-07-26.** The prior version resolved Supabase credentials from
> `op://Claudeclaw/gkg34sopwztoztp6ny6f4cbefy/*` and described EspoCRM as the CRM
> half. Those vault paths are stale (live vault: `rsg_infrastructure`), the
> Supabase MCP already carries its own credentials, and EspoCRM is retired.

---

## Ground truth (verified live 2026-07-26)

| Table | Rows | Role |
|---|---|---|
| `commission_rules` | 216 | Rate rules — how expected commission is derived |
| `portal_carrier_commissions` | 216 | Carrier-portal rate sheet |
| `commission_transactions` | 182 | Statement line items |
| `commission_ledger` | 108 | Expected vs actual, MGA splits, chargeback exposure |
| `commission_ytd` | 15 | Year-to-date rollup |
| `commission_ingest_batches` | 3 | Statement ingest batches |
| `carrier_commission_profile` | 2 | Per-carrier profile |
| `commission_statements` | 1 | Statement headers |
| **`commission_reconciliation`** | **0** | **Empty — no reconciliation has ever been committed.** |

Reporting views: `v_reconciliation_summary`, `v_reconciliation_exceptions`,
`v_comm_by_carrier`, `v_comm_by_line`, `v_commission_by_carrier_month`,
`v_fee_drag`, `v_rule_coverage`, `chargeback_risk_dashboard`,
`commission_parity_report`.

**The headline gap:** 182 transactions and 108 ledger rows exist, but
`commission_reconciliation` is empty. Nothing has been reconciled and committed
yet. Do not describe reconciliation as an operating process — it is built and
unused. Say "no reconciliation committed to date."

---

## Architecture — where the money lives

NowCerts → Supabase → the **standalone Commission Tracker**, which is the
workspace and the place this work actually gets done. It runs private on the
tailnet (`:8446` on the box, container `rsg-commission-tracker-tailnet`), with a
silent shared-account auto-login. The public Cloud Run deployment was deleted.

The CRM cockpit's Commissions view is **read-only and reconciled-only** — it
shows settled money, never drafts. Don't write to it.

The nightly `--sync-commissions` job seeds *expected* commission from won /
in-window business (2026+) off the canonical book.

---

## Access

Use the **Supabase MCP** (project `wibscqhkvpijzqbhjphg`). It carries its own
credentials — do not fetch keys, do not build REST calls by hand. If you ever
genuinely need a raw key, the live vault is `rsg_infrastructure`.

---

## Workflow

1. **Parse the statement.** `hermes/jobs/commission_reconciliation.py`
   (`run_reconciliation`) reads a statement file, builds a policy index, and
   analyzes rows into matched / discrepant / unmatched. It supports `dry_run` —
   **use it first, always.**
2. **Derive expected commission** from `commission_rules` (216 rules). A missing
   rule is a `rate_mismatch` flag, not a guess. Check `v_rule_coverage` before
   assuming the rules cover a carrier.
3. **Upsert `commission_ledger`.** On a duplicate policy + statement row,
   **update the existing entry** — never create a second.
4. **Classify the delta** by tolerance (below).
5. **Open `commission_reconciliation` items** for anything above `matched`, with
   follow-up ownership and recovery tracking.
6. **Summarize open items.** Report what RSG is still owed, largest first.

### Tolerance bands

| Delta | Severity |
|---|---|
| within ±$1.00 | `matched` |
| $1 – $50 | `low` |
| $50 – $200 | `medium` |
| $200 – $500 | `high` |
| > $500 | `critical` |

---

## Hard rules

1. **Money data never auto-commits.** The approval gate is mandatory. Preview,
   get an explicit OK, then write. This mirrors `commission-inbox`.
2. **Never invent a rate.** No rule → flag `rate_mismatch` and stop on that row.
3. **Never create a duplicate ledger entry.** Update on (policy, statement).
4. **Dry-run first.** `run_reconciliation(..., dry_run=True)` is side-effect free
   and returns the same summary text.
5. **The cockpit Commissions view is read-only.** Reconciled money only.

---

## Error handling

| Situation | Action |
|---|---|
| Missing commission rule | Flag `rate_mismatch`; don't estimate. |
| Duplicate policy + statement row | Update the existing ledger entry. |
| Statement file not found | Stop and report the path — no partial run. |
| Unmatched policy numbers | List them explicitly; they are the real finding, not noise. |
| Supabase failure | Retry once, then report to `#systems-check`. |

## Known gaps

- **`commission_reconciliation` is empty (0 rows).** The whole downstream half
  of this skill is untested against real data.
- **`commission_statements` has 1 row** and `commission_ingest_batches` has 3 —
  statement ingest has barely run. See `commission-inbox`.
- **`carrier_commission_profile` has 2 rows** against 216 rate rules; per-carrier
  profiling is essentially unpopulated.
- Reconciliation is invoked manually via `hermes/main.py`; it is not on the
  scheduler.

## References

- `hermes/jobs/commission_reconciliation.py` — the parser and analyzer
- `hermes/sync/commission_sync.py` — seeds expected commission from the book
- `commission-inbox` — statement intake and the approval gate
