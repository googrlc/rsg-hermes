# Hermes AI Operations Center — Operating Constitution

> Hermes is a governed operations automation layer for RSG: auditable workflows,
> deterministic state machines, Slack routing that cannot drift silently, CRM writes
> mediated by a queue and receipts, and finance/renewal artifacts that reconcile to
> source systems instead of trusting free-form prose.

## North Star

Scale Risk Solutions Group to **$1M in annual premium** with a lean team
(Lamar + Gretchen) by automating commission auditing, renewal retention
(Project 85 — 85% retention), CRM data hygiene, and daily operational
intelligence — all governed by guardrails that prevent hallucination,
duplicate records, bad CRM writes, and channel drift.

---

## 1. Hermes Roles

| Role | Scope | CRM Access | Success Criteria |
|------|-------|------------|------------------|
| **HermesCommissionAuditor** | `commission_audits`, `eom_scorecards`, `commission_ledger` | Queue only | Variance explained or zero; EOM rollups balanced; no hallucinated policy numbers |
| **HermesRenewalSpecialist** | `project_85_renewals`, `renewal_actions`, `slack_registry` | Queue only | Action log complete; risk status from enum; escalations to Slack |
| **HermesFinanceOps** | `commission_audits`, `eom_scorecards`, `dashboard_kpis` | None | Scorecard numbers traceable to audits; month lock respected |
| **HermesOpsRouter** | `slack_registry`, `reporting_schedules`, `guardrail_logs` | Queue only | Posts only via registry channels; guardrail on unknown Slack targets |

---

## 2. Slack Channel Registry & Routing Rules

Every outbound or inbound Slack path resolves through `slack_registry` before
automation posts. Rows record purpose and which AI roles may use the channel.

**Operational rule**: if a workflow targets a channel not present or inactive in
the registry, Hermes refuses to post (`BLOCKED_BY_GUARDRAIL`) and logs to
`guardrail_logs`.

| Channel | Purpose | Allowed Roles |
|---------|---------|---------------|
| `#rsg-hermes-commission-audit` | Commission variance alerts, discrepancy threads, auditor handoffs | HermesCommissionAuditor, HermesFinanceOps |
| `#rsg-hermes-project85-renewals` | Project 85 renewal work queue, SLA nudges, carrier escalations | HermesRenewalSpecialist, HermesOpsRouter |
| `#rsg-hermes-operations` | Daily digest, cron health, non-sensitive ops KPI pings | HermesOpsRouter, HermesFinanceOps |

---

## 3. CRM Write Rules & Receipt Formats

> **Corrected 2026-07-26.** This section originally specified `crm_write_queue`
> → `crm_receipts`. Those tables were never built. The **principle** — propose,
> gate, execute, retain proof — is intact and is implemented by
> `outbound_sync_queue`.

Models propose mutations as JSON payloads in `outbound_sync_queue`; an executor
claims approved rows, writes to the destination system, re-reads to verify, and
retains a per-domain receipt.

| Invariant | Rationale |
|-----------|-----------|
| No direct CRM/AMS write from completions alone | Prevents hallucinated entity IDs |
| `approved_by` + `approved_at` required before a row is eligible | A write is a human decision, not a model decision |
| Re-read after write | The receipt's `after_state` / `verified` come from the re-read, not from the request |
| Queue status vocabulary | `queued` → `processing` → `completed` / `failed` — no free-text states |

### Write Path

```
Model output → outbound_sync_queue (queued, approved_by + approved_at set)
    → Scheduler claims (every 5 min, SCHEDULER_ENABLED, one lease holder)
    → Executor validates → reads destination → compares → stops on ambiguity
    → Executes → re-reads to verify
    → Receipt written (e.g. renewal_execution_receipts)
    → Status → completed / failed; failures back off, then dead-letter + alert
```

**Receipts are per-domain, not one global table:**
`renewal_execution_receipts` (renewals). Guardrail decisions go to
`guardrail_logs` (564 rows); sync history to `sync_audit_log` (3,778 rows).

`object_type` routes the job: `renewal`, `intake_crm`, `intake_ams`, `quote`,
`opportunity_writeback`, casework.

---

## 4. Commission Audit, Reconciliation & EOM Scorecards

Operational truth for line-level reconciliation lives in `commission_audits`
(expected vs received, status enum). Summary rollups aggregate into
`eom_scorecards` for a frozen monthly view.

- **Duplicate ingestion rejected**: one row per `(statement_id, policy_number, snapshot_month)`
- **Variance is computed**: `GENERATED ALWAYS AS (received_amount - expected_amount) STORED`
- **EOM lock**: `is_locked` on `eom_scorecards` gates mutations after month close

---

## 5. Project 85 — Renewal Engine Blueprint

Renewal inventory and modeled economics sit in `project_85_renewals`. Every
automated or human-mediated step records a row in `renewal_actions`.

- Risk escalations move `risk_status` through controlled enum transitions only
- `increase_percentage` is a generated column from `premium_current` / `premium_renewal`
- 90/60/30-day checkpoint cadence drives proactive outreach

---

## 6. Reporting Schedule

| Frequency | Report | Slack Target |
|-----------|--------|-------------|
| Daily | Hermes Daily Ops Pulse | `#rsg-hermes-operations` |
| Weekly | Commission Discrepancy Rollup | `#rsg-hermes-commission-audit` |
| Monthly | Renewal SLA Premium Delta | `#rsg-hermes-project85-renewals` |

---

## 7. Dashboard & KPI Table Design

Operational dashboards ingest time-stamped KPI rows into `dashboard_kpis`
(`metric_name`, `metric_value`, `category`). Downstream tooling groups by
category and freshness via `recorded_at`.

| Category | Example Metrics |
|----------|----------------|
| FINANCE | open_commission_audit_exceptions |
| RENEWALS | project85_renewals_at_risk_pct |
| SYSTEM_HEALTH | crm_queue_backlog_age_max_minutes, guardrail_events_24h |

---

## 8. Guardrails

### Prevention Rules

| Rule | Enforcement |
|------|-------------|
| No channel drift | `slack_registry` lookup required; miss = BLOCKED_BY_GUARDRAIL |
| No direct CRM writes | All mutations go through `outbound_sync_queue` |
| No hallucinated policy numbers | Registry + FK + enum constraints |
| No invented states | Strict enums: `sync_status`, `commission_status`, `renewal_risk_status` |
| No duplicate commission rows | Unique index on `(statement_id, policy_number, snapshot_month)` |
| Severity vocabulary | CHECK constraint: LOW, INFO, MEDIUM, HIGH, CRITICAL |

### Database Safeguards

- Nonnegative `attempt_count` on `outbound_sync_queue`
- `ON DELETE SET NULL` for Slack FK on `reporting_schedules`
- `hermes_touch_updated_at()` trigger on mutation-heavy tables
- Partial index `idx_crm_queue_open_work` for worker scanning

---

## 9. Environment Variable Checklist

| Variable | Used For |
|----------|----------|
| `SUPABASE_URL` | Hermes Postgres + PostgREST base URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side Hermes only — full CRUD via RLS role binding |
| `SLACK_BOT_TOKEN` | Slack bot for posting messages |
| `OPENAI_API_KEY` | LLM inference (kept separate from CRM credentials) |

---

## 10. Phased Implementation Plan

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation: Supabase DDL, RLS, edge-case migrations | Complete |
| 2 | Seed `slack_registry` and `hermes_ai_roles`; wire prompt IDs | Complete |
| 3 | Executors: dequeue `outbound_sync_queue`, write to CRM/AMS, persist a per-domain receipt | Live — scheduler drains every 5 min |
| 4 | Commission pipeline: ingest statements, reconcile, lock EOM scorecards | Pending |
| 5 | Project 85 renewals: load renewals, log actions, escalate risk with human gates | Pending |
| 6 | Reporting: schedules + KPI writers; Slack delivery against registry channel IDs | Pending |
| 7 | Hardening: monitor guardrail_logs; tighten authenticated RLS for multi-tenant | Pending |

---

## 11. Supabase Schema Summary

| Table | Purpose |
|-------|---------|
| `slack_registry` | Channel drift control + role allowlists |
| `hermes_ai_roles` | Role definitions with permissions + success criteria |
| `outbound_sync_queue` | Staged CRM/AMS mutations; `queued` → `processing` → `completed`/`failed` |
| `renewal_execution_receipts` | Proof of a renewal write — before/after state, `verified` flag |
| `guardrail_logs` | Guardrail decisions (564 rows) |
| `sync_audit_log` | Sync history (3,778 rows) |
| `commission_audits` | Line-level expected vs received with generated variance |
| `eom_scorecards` | Locked monthly summary rollups |
| `project_85_renewals` | Renewal inventory with generated increase % |
| `renewal_actions` | Append-only action trail for renewals |
| `guardrail_logs` | Blocked action audit trail |
| `reporting_schedules` | Cron-style report configuration |
| `dashboard_kpis` | Time-stamped metric rows by category |
