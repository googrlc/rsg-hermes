# Identity & Permissions Matrix

Concrete operator permissions for RSG's Hermes stack: who can read what, which
write tier applies, and which approval tokens gate intake commits.

**Ground truth:** `agency_crm_users` (human identities), `hermes/agent/nl_agent.py`
(hub tool scoping), `hermes_app/role.py` (process roles), and API `approved_by`
checks in `hermes_app/deps.py`.

Related: [`hermes-tool-map.md`](hermes-tool-map.md) (live agent tools),
[`zoho-supabase-sync-design.md`](zoho-supabase-sync-design.md) (sync direction).

---

## Operator identities (`agency_crm_users`)

Active humans and the machine service account. Only **`.net` emails** in this
table are valid `approved_by`, `owner_email`, `assigned_to_email`, and
`created_by_email` targets — the API rejects anything else.

| Desk | Display name | Email | `role` | Portal access tier | Assignable work? |
|---|---|---|---|---|---|
| **Boss / Lamar** | Lamar Coates | Lamar's `.net` row (see `GET /api/agency-users`) | `administrator` | **Full** (including Settings save) | Yes |
| **Personal Lines / Gretchen** | Gretchen Coates | `gretchen@risksolutionsgroup.net` | `csr` *(label)* | **Full minus Settings save** *(same as Lamar otherwise)* | Yes |
| **Service (machine)** | RSG Service | `lc-rsg@risksolutionsgroup.net` | `service` | Process only — not a portal login | **No** — valid `created_by` / `approved_by` only |

`GET /api/agency-users?assignable=true` excludes `service` so pickers never assign
real work to the bot. `lc-rsg@` still signs automated `approved_by` on queue rows
that a human already approved in conversation.

**Not a CRM user:** display names like `Tia Coates` may appear on legacy AMS
mirror fields (`assigned_to` JSON arrays). They are **not** valid approvers.

### Portal access policy (owner decision, August 2026)

**Gretchen has the same Command Center / portal access as Lamar**, including the
**Knowledge Hub**, all operational desks, finance read surfaces, and full agent
capabilities. The **only** carve-out: she **cannot save Settings** (see below).

Gretchen's `agency_crm_users.role` may still read `csr` in the database — that
column labels her desk, not her portal tier. Effective access follows this table,
not the legacy CSR abbreviation.

---

## Portal surfaces

| Surface | Lamar | Gretchen | Notes |
|---|---|---|---|
| **Knowledge Hub** | Read + contribute | Read + contribute | Document library (`hermes_documents`, Supermemory), domain skills / playbooks (`GET /api/command-center/skills`), indexed agency memory (`client_facts`, `client_notes`), client docs via Nextcloud (`client_documents`) |
| **Command Center desks** | All | All | CRM, Cases, Renewals, Intake, Carrier, Finance — same navigation |
| **Hermes agent (full hub)** | Yes | Yes | All runtime tools when `hub` unset; hub-filtered tools per desk |
| **Finance commission surface** | Read + approve commits | Read + approve commits | Ledger, statements, overrides — `approved_by` as either operator |
| **Settings (save)** | **Yes** | **No** | Commission rules, channel/registry config, reporting schedules, credentials/env panel — Lamar only |
| **Settings (view)** | Yes | Yes | Gretchen may read settings to understand configuration; Save/POST is blocked |

**Settings save** (Lamar only today) maps to mutating configuration, not
day-to-day operations:

| Action | Example route / UI | Gretchen |
|---|---|---|
| Save commission terms | `POST /api/commission-rules` | **Denied** (policy) |
| Edit Slack channel registry | ops constitution tables / future settings UI | **Denied** (policy) |
| Edit reporting schedules | `reporting_schedules` | **Denied** (policy) |
| Change integration credentials | Secrets / env panel | **Denied** (policy) |

> **Implementation note:** parity and the Settings deny are **documented policy**.
> The API today does not distinguish Lamar vs Gretchen on most routes — only
> `approved_by` ∈ `agency_crm_users`. Gate Settings saves on `role =
> administrator` (or an explicit `can_save_settings` flag) when the Settings UI
> lands.

---

## Write tiers

| Tier | Label | Examples | Technical gate |
|---|---|---|---|
| **T0** | Read | Client lookup, renewals list, carrier appetite, commission summary, case queue view | No mutation; hub-scoped agent tools are read-only except `intake_lead` preview |
| **T1** | Single-record portal write | Create one case/task, move one opportunity stage, assign owner on one row | `*_email` fields must be active `agency_crm_users`; RLS + service role on API |
| **T2** | Queue-gated AMS / sync write | Quote push to NowCerts, renewal execution, opportunity writeback, intake AMS stage | Row in `outbound_sync_queue` with `approved_by` + `approved_at`; scheduler executor |
| **T3** | Finance money gate | Commission ledger adjustment, carrier statement commit/reject | `approved_by` on finance routes; `hermes_finance` DB role cannot enqueue AMS writes |
| **T4** | Owner escalation (policy) | Hard-delete case, backdate effective dates, client-facing coverage advice | **Not automated** — Lamar decides; Cases desk persona refuses and routes |

Natural-language `intake_lead` sits between T0 and T1: preview until
`confirmed=true`, then commits through the intake pipeline (and optional Zoho
when `HERMES_WRITE_TO_ZOHO=1`).

---

## Role matrix

### Boss / Lamar (`administrator`)

| Dimension | Allowed |
|---|---|
| **Read scopes** | All Command Center desks; **Knowledge Hub**; full agent when `hub` unset; bearer-gated ops (`/api/hermes/book-sync`, AMS search when `HERMES_API_TOKEN` set); finance commission surface |
| **Write tier** | T0–T3; **T4** owner decisions per agency policy |
| **Settings** | **Save allowed** — configuration and integration settings |
| **Default pipeline ownership** | Commercial and unrecognized LOBs (`hermes/sync/quote_sync.py`) |
| **Approval tokens (intake)** | All tokens below; production default **`APPROVE ALL`** |
| **Typical `approved_by`** | Lamar's `.net` email |

### Personal Lines / Gretchen (`csr` label — full operator access)

| Dimension | Allowed |
|---|---|
| **Read scopes** | **Same as Lamar** — all desks, **Knowledge Hub**, finance surface, full agent |
| **Write tier** | **Same as Lamar** — T0–T3 on operational work (cases, pipeline, intake, queue-gated AMS, finance approvals) |
| **Settings** | **View only — cannot save** (commission rules, registry, schedules, credentials) |
| **Default pipeline ownership** | Personal lines LOBs (Personal Auto, Homeowners, Motorcycle, Dwelling Fire, Condo, Personal Umbrella, MAPD, Life — see `_PERSONAL_LINES` in `quote_sync.py`) |
| **Approval tokens (intake)** | Same vocabulary as Lamar; **`APPROVE ALL`** for standard intake |
| **Typical `approved_by`** | `gretchen@risksolutionsgroup.net` |
| **Persona / SOP** | Cases desk persona may still route **coverage advice** to Lamar for client-facing language — that is workflow tone, not a portal deny |

### Service (`lc-rsg@risksolutionsgroup.net`)

| Dimension | Allowed |
|---|---|
| **Read scopes** | Same APIs the hub process calls; not a human desk |
| **Write tier** | T1 as `created_by` on automated rows; T2 only when a human's approval is already recorded on the queue row |
| **Approval tokens** | Does not approve intake in UI — humans approve; may appear as `approved_by` only when mirroring a signed human decision in automation |
| **Assignable work** | Never |

---

## Hermes AI hub read scopes

When the Command Center passes `hub=…`, the conversational agent only receives
tools for that desk (`hermes/agent/nl_agent.py` → `_HUB_TOOLS`).

| Hub | Runtime tools (read) | Write via agent |
|---|---|---|
| `crm` | `find_client`, `client_policies`, `ams_client_snapshot`, `crm_client_activity`, `client_documents`, `renewals_overview` | None (read-only desk) |
| `renewals` | `renewals_overview` + CRM read tools above | None |
| `cases` | `list_cases`, `case_progress` + CRM read tools above | None (case API writes exist but are not agent tools yet) |
| `intake` | `list_intake_submissions` | None |
| `carrier` | `list_carriers`, `match_carrier_appetite`, `lookup_class_code`, `appointments_by_line`, `web_research` | None |
| `finance` / `commissions` | `commission_summary`, `commission_shortfalls` | None |
| *(unset / full)* | All tools in [`hermes-tool-map.md`](hermes-tool-map.md) | `intake_lead` only (preview → confirm) |

Portal and MCP writes use HTTP routes and MCP tools (`create_case`, `create_task`,
`send_to_nowcerts`, …) — not this hub table.

---

## Approval tokens (intake commit)

Canonical set: `hermes/commands/agency_intake.py` → `ALLOWED_APPROVAL_TOKENS`.
Persisted on `intake_submissions.approval_token`; worker branches in
`hermes/operations/intake_worker.py`.

| Token | Effect on commit | Typical use |
|---|---|---|
| **`APPROVE ALL`** | Supabase opportunities + retrieval rows + CRM-side writes per worker branches | Default; Lamar's standard path |
| **`APPROVE CRM ONLY`** | CRM/pipeline writes; skips Supabase retrieval/RAG inserts | Privacy or partial publish |
| **`APPROVE SUPABASE ONLY`** | Retrieval/Supabase rows; skips CRM enqueue path | Index without CRM mutation |
| **`APPROVE TASKS ONLY`** | No-op write path today (reserved) | Future task-only lane |
| **`REVISE`** | Returns draft to author | Fix before commit |
| **`CANCEL`** | Aborts pending draft | Drop bad intake |

`approved_by` is **required** with any approval token and must be an active
`agency_crm_users` email. The API does **not** yet restrict which token a given
`role` may issue — that is policy (table above), not code.

---

## Process roles (`HERMES_ROLE`) — containers, not people

Orthogonal to Lamar/Gretchen. Gates which **process** may hold NowCerts creds and
which routers load.

| `HERMES_ROLE` | `HERMES_SERVICE` | NowCerts creds | Postgres role | Purpose |
|---|---|---|---|---|
| `write_in` | `all`, `hub` | **Required** | `hermes_write` | NowCerts core: book sync, `outbound_sync_queue`, portal write log |
| `finance_readout` | `finance` | **Forbidden** | `hermes_finance` | Commission surface read + money gates; no queue enqueue |
| `mirror_reader` | `intake`, `renewals`, `carriers` | **Forbidden** | `hermes_mirror_reader` | Split services read mirror; enqueue writes back to hub |

---

## What code enforces today

| Rule | Where |
|---|---|
| `approved_by` / `*_email` ∈ active `agency_crm_users` | `hermes_app/deps.require_users` |
| Service account hidden from assignee pickers | `GET /api/agency-users?assignable=true` |
| Hub tool subset | `nl_agent._scoped_tools` |
| NowCerts creds only on `write_in` | `hermes_app/role.py` |
| Finance DB role cannot write `outbound_sync_queue` | `supabase/migrations/20260810120000_hermes_role_grants.sql` |
| Intake token vocabulary | `ALLOWED_APPROVAL_TOKENS` + intake router validation |
| Bearer on privileged HTTP routes | `HERMES_API_TOKEN` on selected `/api/hermes/*` routes |

## Policy not yet role-gated in code

- **Settings save** — Gretchen deny is policy-only until Settings UI + API gate ships.
- Which operator may issue each intake approval token (Lamar vs Gretchen) — both may use all tokens per owner policy above.
- LOB ownership enforcement on writes (enforced by convention + sync defaults, not RBAC).
- Zoho CRM module permissions (Zoho-side; Hermes uses OAuth service user).
- T4 escalations (persona + SOP, not API deny rules).

---

## Quick reference for implementers

```bash
# Who can approve / be assigned?
curl -s http://127.0.0.1:8787/api/agency-users
curl -s 'http://127.0.0.1:8787/api/agency-users?assignable=true'

# Queue freshness (AMS writes)
curl -s http://127.0.0.1:8787/api/hermes/sync-health
```

When adding a new write path: name the tier (T1–T3), require `approved_by` if T2+,
and update this matrix if operator policy changes.
