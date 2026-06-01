# Renewal Pipeline — Ownership Map (source of truth)

**Why this file exists:** renewal automation has accreted across *three generations*
that silently overlapped, so fixes kept "drifting into old builds." This map
declares which system owns each stage. **Anything not on this map is legacy —
retire it on sight; do not rebuild it elsewhere.**

This file is mirrored in **rsg-espocrm** and **rsg-hermes** (both systems are
live). Keep them identical.

## The three generations
- **Gen 1 — n8n (c6ktp):** WF1–WF5, NB-WF1, M2 EOB, Account Rollup, Retention
  Report, commission flows. **Retired** — archived to
  `rsg-infrastructure/n8n-workflows/archive/`, deleted from n8n.
- **Gen 2 — EspoCRM native (RsgCore module):** scheduled jobs + hooks. **Live.**
- **Gen 3 — Hermes (`hermes/renewals/`):** task + worksheet + completion. **Live.**

## Ownership

| Stage | Owner | Implementation |
|---|---|---|
| Create Renewal record from an expiring policy | **EspoCRM (native)** | `SyncRenewalsFromPolicies` scheduled job (daily 6am) + `Policy/ActivateAutomation` hook → `RenewalOrchestrator::syncFromPolicy()` |
| Derive renewal fields (premium / LOB / carrier / expiration / urgency) | **EspoCRM (native)** | `Renewal/DeriveFields` hook → `RenewalOrchestrator` |
| Create the **work task** (rich worksheet card, assigned to Gretchen) | **Hermes** | `hermes/renewals/sweep.py` (`hermes --renewal-sweep`) |
| Worksheet UI + required-field enforcement (Won can't be faked) | **EspoCRM** | `Renewal` entityDefs (4 checkbox bools) + `clientDefs` dynamic logic + detail layout |
| Task completion → file worksheet (Google Doc) + Slack card | **Hermes** | `hermes/renewals/complete.py` via `POST /renewals/complete` (fired by EspoCRM `Task/SendServiceWebhook` dispatcher) |
| Slack "Acknowledge" button (idempotent) | **Hermes** | `hermes/integrations/slack_socket.py` (`renewal_ack_*`) |
| Commission on won (renewal & new business) | **EspoCRM (native)** | `Renewal/CreateCommissionLedger` + `Opportunity/CreateCommissionLedger` hooks (idempotent upsert) |
| Renewal reporting / 90-60-30 checkpoints | **Hermes** | `revenue_sentinel` (+ `eom_scorecard`, `revenue_integrity`) |

## Explicitly retired (do NOT rebuild)
- All Gen-1 n8n renewal/commission/rollup/sync workflows (archived + deleted).
- **Native bare-task creation** — `RenewalOrchestrator::createInitialTaskIfMissing`
  is **disabled**. It produced cardless, unassigned tasks that fought the Hermes
  sweep. **Hermes owns task creation.** EspoCRM still owns Renewal-record creation.
- NowCerts↔EspoCRM sync in n8n — Hermes owns NowCerts sync.

## The rule
A renewal flows: **EspoCRM creates the record → Hermes creates the task →
EspoCRM enforces the worksheet → Hermes handles completion → EspoCRM books the
commission → Hermes reports.** One owner per stage. If you find a second thing
doing a stage on this map, it's drift — retire it.
