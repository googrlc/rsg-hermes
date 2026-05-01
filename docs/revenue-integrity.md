# Revenue Integrity & Accounting

Hermes Revenue Integrity jobs protect commission visibility and accounting hygiene.

## Commission Audit Sentinel

`hermes --commission-audit` scans `Policy` records and flags revenue blind spots:

- Policy status in `Bound` or `Active`
- Commission rate/percentage missing or `0`

Slack output includes an `Update Commission %` button per policy. Button actions are handled by Hermes Socket Mode and create an Espo `Task`.

## Commands

- `hermes --commission-audit`: run once and post to Slack.
- `hermes --commission-audit-dry-run`: render output without posting.
- `hermes --commission-audit-force`: bypass same-day duplicate-post guard.

## Config

- `HERMES_COMMISSION_AUDIT_CHANNEL`: Slack destination channel (falls back to default notifier channel if unset).
- `HERMES_COMMISSION_AUDIT_STATE_FILE`: idempotency state path.
- `HERMES_COMMISSION_TASK_ASSIGNEE_ID`: optional assignee for update tasks.

## End-of-Month Scorecard

`hermes --eom-scorecard` posts a previous-month report with:

- Total Premium Bound
- Agency Revenue (estimated from commission amount/rate)
- New Business vs Renewals split
- Retention percentage
- North Star progress toward premium goal

Commands:

- `hermes --eom-scorecard`
- `hermes --eom-scorecard-dry-run`
- `hermes --eom-scorecard-force`

Config:

- `HERMES_EOM_SCORECARD_CHANNEL`
- `HERMES_EOM_SCORECARD_STATE_FILE`
- `HERMES_NORTH_STAR_PREMIUM_GOAL` (default `1000000`)

## Commission Reconciliation (Carrier Hunter)

`hermes --commission-reconcile-file <path>` compares carrier statement payouts against expected CRM commission.

Supported input formats:

- CSV/TXT
- XLSX (requires `openpyxl` in runtime)
- PDF (requires `pypdf` in runtime; parsed heuristically)

Discrepancies are posted to Slack with a `Create dispute task` button.

Config:

- `HERMES_COMMISSION_RECON_CHANNEL`
- `HERMES_COMMISSION_RECON_RULE` (`any_difference`, `percent_over_1`, `dollar_over_25`, `hybrid_1pct_or_25`)
- `HERMES_COMMISSION_RECON_PERCENT_THRESHOLD`
- `HERMES_COMMISSION_RECON_AMOUNT_THRESHOLD`

