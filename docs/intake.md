# Intake Architecture (source of truth)

**Hermes owns intake end-to-end.** A submission enters at `POST /api/intake`
(envelope → Supabase `intake_submissions`, idempotent on `idempotency_key`), and the
`hermes-intake-worker` compose service drives the state machine:
`received → synthesizing → synthesized → drafting → awaiting_approval` → (Slack APPROVE)
`→ approved → writing → written → complete`. Extraction is an **LLM call inside Hermes**
(`commands/agency_intake.py`, the `crm-intake-writer` contract), producing a structured
`draft_summary` (account + contacts + per-LOB opportunities + note + facts) and a
Slack-facing `hermes_blocks` render. Routing is by classification/LOB
(`account_type=Personal Lines` → Gretchen's review); the per-lane YAMLs in
`command_center/lanes/` belong to the **separate** Command-Center web-UI flow, not the
`/api/intake` path. There is **no Paperclip in the intake path, no ATTOM enrichment, and
no completeness-gate exception** — these are intentionally absent; incomplete-but-valid
input yields a thin draft (never fabricated), and truly empty payloads are rejected at
the door (`422`).

**Paperclip is reserved for future multi-mission orchestration**, not intake. It earns
its place when a *second concurrent workflow* needs coordination with the first
(e.g. the commercial lane running alongside personal lines, or parallel agent missions).
Until then it runs idle on `rsg-runtime` and that is fine — inserting it into today's
single linear pipeline would add a manager to a one-person conveyor belt. Its entry
ticket is *defined, not deleted*: wire it in when concurrency arrives.

See [`e2e_audit_report.md`](e2e_audit_report.md) for the 2026-06-12 end-to-end audit of
this pipeline (verdict: extraction is quote-ready and fabrication-free; the Slack
draft-delivery channel was misconfigured and must be fixed before go-live).
