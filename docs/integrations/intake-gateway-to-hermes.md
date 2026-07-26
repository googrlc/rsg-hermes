# Build spec — intake gateway → Hermes pipeline

**Audience:** an AI agent working in the **`rsg-nowcerts-write-gateway`** repo
(deployed as the `rsg-intake-gate` container).
**Owner of this contract:** rsg-hermes. If the two disagree, this file wins.
**Written 2026-07-26**, verified against both running containers.

Build one thing: when an intake produces per-LOB opportunities, POST them to
Hermes. Nothing else in either system changes.

> ## ⚠ Re-verify §2 before you start
>
> The gateway was **mid-update** when this was written. §1 (network, client,
> base URL) and §3–§7 (the Hermes contract) are owned by Hermes and are stable.
> **§2 describes the gateway's own output shape as observed on 2026-07-26** and
> is the part most likely to have moved.
>
> First action, before writing any code:
>
> ```bash
> grep -rn "crm_records" src/                       # expected: no matches
> grep -n "deferred" src/intake/intake-builder.js   # expected: the return block
> ```
>
> - **Both as expected** → build exactly as written.
> - **`crm_records` now exists** → the gateway has shipped its own CRM block.
>   Map *that* to §3's payload instead of `deferred`; §4's field mapping and
>   every rule in §5–§7 still apply unchanged.
> - **`deferred` is gone and nothing replaced it** → stop and ask. Don't
>   reconstruct the opportunity list from `pdf_record`.

---

## 1. What already exists (verified, do not rebuild)

| Fact | Evidence |
|---|---|
| The two containers share a Docker network | `rsg-intake-gate` is on `app_default`, `hermes-shared`, `rsg-intake-gate_default`; `rsg-hermes-api` is on `hermes-shared` |
| Hermes is reachable by name | `getent hosts hermes-api` → `172.16.1.3` from inside the gateway |
| The base URL is already configured | `HERMES_PREVIEW_URL=http://hermes-api:8787` |
| A Hermes client already exists | `src/connectors/hermes-preview.js` → `HermesPreviewClient`, with a private `#post(path, body)` and two live methods (`stageDraft` → `/agency-intake`, `researchBusiness` → `/dispatch`) |
| No auth header is needed | The API is unauthenticated on the internal network / tailnet. Do not invent a bearer token. |

**Add a method to the existing client. Do not write a new HTTP layer, a new
config var, or a second base URL.**

---

## 2. The actual gap

`src/intake/intake-builder.js :: runIntake()` returns:

```js
{
  status: "INTAKE_DRAFTED",
  submitted_by,
  insured: { status: "PREPARED", prepared, search },   // → NowCerts, already wired
  pdf_record,                                          // → evidence, already assembled
  deferred: [ { entity, role, write_target } ],        // ← NOTHING CONSUMES THIS
  pdf_generation: "deferred",
  archive: "deferred",
}
```

The code comment says it plainly:

> *Contacts and opportunity are captured now and queued entity-by-entity in a
> later reviewed stage; they are shown in the draft but not yet prepared.*

**That later stage is what you are building**, for the opportunity records only.

### Scope

- **In scope:** `deferred` entries whose record is an opportunity / whose
  `write_target` is the CRM → `POST /api/opportunities`.
- **Out of scope:** contacts (no Hermes contact endpoint exists yet), the
  insured (already goes to NowCerts), the PDF record (stays on the document).

> A prior draft of this integration specified a `crm_records` block with
> `destination: "hermes"` and a `nowcerts_write: "manual"` tripwire field.
> **That block does not exist** — grep `src/` for `crm_records` returns nothing.
> Do not build it. Use the `deferred` records you already have and the endpoint
> contract in §3.

---

## 3. The Hermes endpoint

### `POST /api/opportunities`

```jsonc
{
  "line_of_business": "General Liability",   // REQUIRED
  "insured_name": "Truecraft Drywall & Painting",  // required unless client_identifier
  "fein": "12-3456789",                      // optional; sharpens the dedupe key
  "insured_id": "c45051bd-...",              // NowCerts insured GUID, when known
  "opportunity_type": "New Business",
  "premium_estimate": 8400,
  "carrier": "Progressive",
  "assigned_to": "[\"Lamar Coates\"]",
  "description": "Source: 2026-07-25 intake call. Needs GL + WC. Payroll not supplied.",
  "source": "intake-gate"
}
```

**Response `200`:** `{ "ok": true, "created": true|false, "opportunity": { ... } }`
**`400`** — bad vocabulary (unknown `opportunity_type`, or a `stage` outside the
type's set), or neither `client_identifier` nor `insured_name` supplied.
**`502`** — Hermes-side failure. Retry is safe (see §6).

### Send only what you have

Every field except `line_of_business` and the client identity is optional.
**Omit `stage`** — Hermes defaults it correctly per type. **Omit `probability`
and `likelihood`** — they derive from the stage. Sending a guess is worse than
sending nothing.

`referral_source` is **not settable** here; it is read-only, owned by the AMS sync.

### Vocabularies — exact strings, 400 on anything else

`opportunity_type`: `New Business` · `Renewals` · `Cross-selling` · `Upselling` ·
`Remarket` · `Bundling` · `Competitive Replacements (BOR)` · `Life Events` ·
`Seasonal / Event`

`line_of_business` is free text, but **match what the book already uses** or the
pipeline fragments: `Personal Auto`, `Commercial Auto`, `Homeowners`,
`General Liability`, `Worker's Compensation`, `Professional Liability`,
`Commercial Property`, `Motorcycle`. Note the apostrophe in *Worker's*.

---

## 4. Field mapping

From a reconciled `deferred` record to the payload. `valueMap` is the
field→value map the builder already constructs.

| Hermes field | Source |
|---|---|
| `line_of_business` | the record's LOB — **one row per LOB, never bundled** |
| `insured_name` | `insuredDisplayName(valueMap)` — the same helper the insured proposal uses |
| `fein` | the FEIN field, digits or formatted; Hermes strips non-digits |
| `insured_id` | `insured.prepared` NowCerts `database_id` **once it exists**; otherwise omit |
| `premium_estimate` | current/target premium if parsed, else omit |
| `carrier` | incumbent carrier if parsed, else omit |
| `opportunity_type` | `New Business` for a new prospect; `Cross-selling` for an existing client |
| `description` | **the provenance string** — see below |
| `source` | the literal `"intake-gate"` |

### Provenance goes in `description`

Hermes has **no per-field citation column.** The gateway's rich
`source: {kind, reference, location, excerpt, captured_at}` cannot be stored
field-by-field. Flatten it into one human sentence:

```text
Source: <kind> "<reference>" captured <captured_at>. <n> field(s) needed review: <field list>.
```

Do not JSON-stuff the citation objects into `description`. A human reads this.

### Unresolved fields

If the record has `missing_fields` or `conflicts`, still create the
opportunity — a deal that exists only in a PDF is the failure mode this
integration removes — and name the gaps in `description`. Do **not** invent a
stage to signal incompleteness; `Preparing Application` is already the default.

---

## 5. Owner assignment

`assigned_to` is **required in practice**: 49 of 63 live rows have no owner, and
an unowned opportunity is how a renewal goes dark.

| Condition | Value |
|---|---|
| Personal lines | `"[\"Gretchen Coates\"]"` |
| Commercial, any LOB | `"[\"Lamar Coates\"]"` |
| Unclear | `"[\"Lamar Coates\"]"` and say so in `description` |

**The format is a JSON array encoded as a string** — NowCerts' shape, mirrored.
Not an email. Not a bare name.

**Do not confuse it with `approved_by`**, which appears on other Hermes
endpoints and must be an active `.net` email validated against
`agency_crm_users` (`lamar@risksolutionsgroup.net`,
`gretchen@risksolutionsgroup.net`, `lc-rsg@risksolutionsgroup.net`). A `.com`
address is a 400. Fetch the live list from `GET /api/agency-users`; never
free-type it. **`POST /api/opportunities` does not take `approved_by` at all.**

---

## 6. Idempotency — read this before adding retries

Hermes enforces:

```sql
uq_opportunities_client_lob_type UNIQUE (client_identifier, line_of_business, opportunity_type)
```

`client_identifier` is derived server-side from `insured_name` (+ `fein`):
lowercased, non-alphanumerics → hyphens, `:<fein-digits>` appended when present.
**Let Hermes derive it.** Computing it yourself risks a near-miss slug and a
duplicate the constraint cannot catch.

Therefore:

- **`created: false` is success**, not failure. It means the opportunity already
  existed and was returned. Log it as *adopted*; never retry it as an error and
  never surface it to a user as a problem.
- **Re-POSTing is safe.** A retry after a timeout returns the existing row.
- **Never `PATCH` an adopted row to "fix" it.** Any `PATCH` or `/stage` call sets
  `sync_source='crm'`, after which the inbound AMS sync **permanently stops
  updating that opportunity.** The gateway must not trigger that as a side
  effect of an intake.

---

## 7. Hard rules

1. **Never write to Supabase directly.** The gateway has no Supabase credentials
   and must not be given any. Go through the API.
2. **One opportunity per LOB.** Six LOBs → six POSTs. Never a combined
   "Commercial Package" row.
3. **Never create a NowCerts quote or policy from this path.** Opportunity
   creation is Supabase-only. The AMS quote push is a separate, approval-gated
   Hermes endpoint (`/send-to-nowcerts`) and is **not** yours to call.
4. **Partial success is success.** If four of six POSTs land, report four
   created and two failed with reasons. Do not roll back the four.
5. **Never block the intake on Hermes.** If Hermes is down, the insured proposal
   and PDF record must still complete. Opportunities are additive.

---

## 8. Implementation

**Step 1 — extend the client** (`src/connectors/hermes-preview.js`):

```js
async createOpportunity(payload) {
  return this.#post("/api/opportunities", payload);
}
```

That is the whole transport change. `#post` already handles JSON, timeout, and
error extraction (`value.detail ?? value.message`), which matches Hermes'
FastAPI error shape.

**Step 2 — map and fan out.** A pure function, unit-testable without network:

```js
export function toOpportunityPayloads(intakeResult) { /* → array of payloads */ }
```

One payload per opportunity-bearing `deferred` record, per §4.

**Step 3 — call it** after the insured proposal resolves, so `insured_id` can be
attached when available. Fan out with `Promise.allSettled` — one failure must not
sink the others.

**Step 4 — report** in the intake result. Extend the return rather than replacing
it:

```js
crm: {
  attempted: 6,
  created: 4,
  adopted: 1,          // created:false — already existed
  failed: [ { line_of_business, status, detail } ],
}
```

**Step 5 — flag it off by default.** `HERMES_CRM_WRITES=1` to enable. Ship dark,
verify against one real intake, then turn it on.

---

## 9. Acceptance criteria

- [ ] Six-LOB commercial intake → six opportunities, one per LOB, all owned.
- [ ] Re-running the same intake → `adopted: 6`, `created: 0`, **no duplicates**
      in `opportunities`.
- [ ] Personal-lines intake → owner is Gretchen; commercial → Lamar.
- [ ] Hermes stopped → intake still returns `INTAKE_DRAFTED` with the insured
      proposal and PDF record intact; `crm.failed` lists the reason.
- [ ] Unknown `opportunity_type` → 400 surfaced with Hermes' `detail`, not
      swallowed.
- [ ] `toOpportunityPayloads()` has unit tests covering: one-per-LOB fan-out,
      owner selection, provenance string, omitted-vs-null optional fields.
- [ ] No `PATCH`, no `/stage`, no `/send-to-nowcerts` call anywhere in the path.

## 10. Verify against the live system

```bash
# from inside the gateway container — reachability
docker exec rsg-intake-gate sh -c 'getent hosts hermes-api'

# valid owners / approver identities
curl -s http://hermes-api:8787/api/agency-users

# what the pipeline holds right now
curl -s 'http://hermes-api:8787/api/opportunities?status=open&limit=5'
```

From a workstation on the tailnet, swap the base for
`https://hermes-gretch.tail1cbc83.ts.net:8444`. The Kanban at `/cockpit#pipeline`
shows the result immediately — use it to eyeball the first real run.

## 11. Related

- `.claude/skills/hermes-crm-writer/SKILL.md` — the pipeline write contract in
  full (stage vocabularies, both AMS paths, stop conditions)
- `hermes/api.py` — `OpportunityCreateRequest`, `_require_users`
- `hermes/intake/opportunities.py` — `create_opportunity`, the vocabularies
