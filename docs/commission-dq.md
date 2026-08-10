# Commission DQ / AMS anomaly scan

Weekday report-only job that compares the AMS book mirror, the commission
rulebook, and `commission_ledger` for row-level anomalies. It does **not** write
to NowCerts or auto-fix ledger rates/status.

## Commands

```bash
hermes --commission-dq              # scan + post (once per calendar day)
hermes --commission-dq-dry-run      # full report, no post
hermes --commission-dq-force        # bypass daily idempotency guard
hermes --commission-dq-limit 50     # cap findings in the report
```

On `hermes-gretch`:

```bash
cd /opt/rsg-hermes && git fetch && git checkout main && git pull
# rebuild hermes CLI image as usual, then:
docker compose run --rm -e PYTHONUNBUFFERED=1 hermes hermes --commission-dq-dry-run
```

## Cadence

Weekdays **6:15am ET** via `deploy/cron/hermes.crontab` (after the 5:45am agency
snapshot, before the 8:00am revenue sentinel).

## Channel / state

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_COMMISSION_DQ_CHANNEL` | falls back to audit / alert / systems-check | Talk/Slack destination |
| `HERMES_COMMISSION_DQ_STATE_FILE` | `.hermes/commission_dq_state.json` | One-post-per-day guard |
| `HERMES_COMMISSION_SINCE` | `2026-01-01` | Seed window for DQ-NB2 (same as `--sync-commissions`) |

## Checks (v1)

| ID | Rule | Severity |
|---|---|---|
| DQ-NB1 | Ledger `is_renewal` disagrees with AMS (`renewed_policy` / business_type / renewal status) | High |
| DQ-NB2 | Active/Renewed commissionable book row with no ledger match (seed-window aware) | High |
| DQ-RATE1 | AMS `agency_commission_amount` vs ledger `expected_commission` differs by >$5 or >5% | High |
| DQ-RATE2 | Renewal matched NB-only rate (or NB matched renewal-only), from rule percents / arithmetic | High |
| DQ-TIME1 | Carrier `payment_model` is `as_earned` but rule/ledger timing is advance (or reverse) | Med |
| DQ-BILL1 | Active/Renewed ledger missing `billing_type` while canonical has one | Med |
| DQ-BILL2 | Agency Bill ledger row with null/`0` `agency_fee_amount` (AMS list often omits fee) | Info |
| DQ-BLIND | `expected_commission` null/≤0 on non-chargeback ledger rows | High |

Known live Agency Bill examples that may show **DQ-BILL2**: Brown-Farmer
`EHJ-ADO00825576` / `EGK-ADO00825579`.

## Data sources

Prefer mirrors (no mandatory live AMS pull in v1):

- `canonical_policies`
- `commission_ledger`
- `commission_rules`
- `carrier_commission_profile`

## Out of scope (v1)

- Auto-fix AMS or ledger
- Portal UI for findings
- Statement-parse anomalies
- Enabling `--commission-watchdog` / `--commission-reconcile` cron (follow-up)
- Replacing revenue sentinel or EOM scorecard

See also: [Revenue Integrity](revenue-integrity.md), billing enums in
[NowCerts import mapping](integrations/nowcerts-import-mapping.md).
