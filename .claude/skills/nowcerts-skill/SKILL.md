---
name: nowcerts-skill
description: >
  NowCerts (Momentum) AMS reference for RSG policy and insured data — the system
  of record for who a client is and what they have bound. Covers the MCP tools to
  reach it, the canonical Supabase mirror to prefer for scans, the real endpoint
  names, renewal buckets, and the write surface. Use whenever you need policy
  facts, renewal windows, expiring-policy scans, or insured lookups.
---

# NowCerts (Momentum) — the AMS

NowCerts is the source of truth for bound policy data and client identity.
"Momentum" is the legacy name for the same API, not a second system.

> **Rewritten 2026-07-26.** The prior version told you to mint a token from
> `op://Claudeclaw/NowCerts/*` and hand-roll HTTP against `PolicyList`. Those
> vault paths are stale (the live vault is `rsg_infrastructure`), the MCP servers
> already carry their own credentials, and the endpoint the code actually calls
> is `PolicyDetailList`.

---

## Reach it through the tools — you do not need credentials

The MCP servers are launched with their own injected credentials. **Do not fetch
keys, do not mint tokens, do not hand-roll HTTP** unless you are debugging the
client itself.

| Need | Use |
|---|---|
| Find an insured | `mcp__rsg-hermes__ams_search_insured` |
| Create an insured | `mcp__rsg-hermes__ams_create_insured` |
| Upsert a policy | `mcp__rsg-hermes__ams_upsert_policy` |
| Raw AMS reads/writes | the `nowcerts` MCP server (`/mcp/nowcerts`): `search_insureds`, `list_policies`, `create_insured`, `create_insured_with_policies`, `insert_policy`, `update_policy` |
| Bulk scans, premium totals, renewal windows | **the Supabase mirror** — see below |

**HTTP gotcha:** MCP-over-HTTP always returns HTTP 200. Auth failures come back
in the JSON-RPC body as `-32001 Unauthorized`. Never key a smoke test on the
HTTP status — read the body.

---

## Prefer the canonical mirror for anything at scale

Paging the AMS for every scan is slow and rate-limited. The book is mirrored
into Supabase and read via
[hermes/ams/book.py](../../../hermes/ams/book.py).

| Table | Rows (2026-07-26) | Use |
|---|---|---|
| `canonical_policies` | 618 (163 flagged `active`) | Premium, LOB, carrier, dates, lineage |
| `canonical_clients` | 415 | Client roster |
| `nowcerts_insured_mirror` | — | Insured-level mirror |

Useful `canonical_policies` columns: `policy_guid` (unique),
`nowcerts_insured_guid`, `policy_number`, `lines_of_business`, `carrier`,
`status`, `active`, `effective_date`, `expiration_date`, `premium_amount`,
`annualized_premium`, `current_term_amount`, `agency_commission_amount`,
`renewed_policy` (the lineage pointer), `state`, `agents`, `csrs`.

### ⚠ The mirror is currently contaminated

48 rows carry a literal tombstone status — `Inactive: not in NowCerts 2026-07-21`
(43) and `...2026-07-23` (5) — written by the `rsg-import` pg_cron path, which
pulled `is_quote=false` only and marked everything it didn't see as gone. It was
**disabled 2026-07-24** and needs a single writer before it is re-enabled. A
further 5 rows are `status='Expired'` with `active=true`, and 2 are `'Renewed'`
with `active=true`.

**Consequence:** the tombstoned rows are `active=false` and carry **$378,575**,
so they are *excluded* from any active-premium total — the mirror **understates**
the book by up to that much. Either verify against the AMS directly or state the
caveat. When the mirror and NowCerts disagree, **NowCerts wins.**

`policy_guid` is unique — `canonical_policies` has no true duplicate rows. What
looks like duplication is overlapping *terms*, resolved through `renewed_policy`
lineage.

---

## Real endpoints (for debugging the client only)

Base `https://api.nowcerts.com`, token at `/api/token`
(`grant_type=password`, `client_id=ngAuthApp`). Retry once on 401.

**Reads:** `/api/InsuredDetailList`, `/api/InsuredList`,
`/api/PolicyDetailList`, `/api/OpportunitiesList`.

> `PolicyDetailList` — **not** `PolicyList`. The old skill named the wrong one.

**Writes:** `/api/Insured/Insert`, `/api/InsuredAndPolicies/Insert`,
`/api/Policy/Insert`, `/api/Policy/PartialUpdate`,
`/api/Zapier/InsertOpportunity`, `/api/Zapier/InsertTask`,
`/api/Zapier/UpdateTask`.

`Insured/Insert` upserts on CommercialName / FirstName+LastName, so re-runs
don't duplicate insureds.

**Known write failure:** `POST /api/Zapier/InsertOpportunity` returned
`400 {"message":"Can't assign to Insured/Prospect"}` on the one opportunity
writeback ever attempted (2026-07-20). Required-field guards were added
afterward; no successful writeback exists yet.

---

## Key fields

`databaseId` (primary key) · `commercialName` · `firstName` / `lastName` ·
`policyNumber` · `carrierName` · `lineOfBusiness` · `premium` ·
`effectiveDate` · `expirationDate` · `active` · `insuredDatabaseId`

### Name resolution

- Use `commercialName` when present; otherwise `firstName + lastName`.
- **Prefer `databaseId` or a policy identifier over name matching.** The book
  has known duplicate clients (`momentum_client_id` groups) and orphaned
  contacts — verify a match before you mutate.
- RSG's `policy_number` in Supabase is formatted `Client | Line of Business |
  Number`; parse the LOB out of it rather than inferring.

---

## Renewal buckets

| Days to expiration | Bucket |
|---|---|
| 0–14 | CRITICAL |
| 15–30 | URGENT |
| 31–60 | WATCH |
| 61–90 | PIPELINE |

Entry thresholds: **commercial 60 days, personal lines 30 days.** The cockpit's
forward window is wider — 120 days commercial, 30 personal.

These buckets are for *describing* an expiry horizon. They are **not** the
retention risk model — that is `classify_risk` in `retention-risk-scout`
(`SAFE` / `AT_RISK` / `CRITICAL`), which weighs premium increase first.

---

## Write discipline

1. **Read the record first, confirm it's the right one, then write.** Data is
   dirty: duplicate clients, orphaned contacts, overlapping terms.
2. **Never overwrite a populated field.** Fill-blank only; a conflict goes to a
   human.
3. **Policies are not created from the CRM side.** They arrive by carrier
   download or are entered in the AMS by a person.
4. Renewal and quote writes go through the approval-gated `outbound_sync_queue`,
   never a direct synchronous call. See `renewal-desk` and `hermes-crm-writer`.

---

## Error handling

| Situation | Action |
|---|---|
| Token mint fails | Report to `#systems-check` and stop. |
| `401` | Re-mint once, then stop. |
| Empty policy result | Verify the insured GUID, retry once, then say "no policies found" — don't infer the client is inactive. |
| Mirror and AMS disagree | **NowCerts wins.** Flag the drift. |
| Need expirations but only have insured data | Switch to `PolicyDetailList`. |

## References

- `hermes/sync/nowcerts_client.py` — the client and every real endpoint
- `hermes/ams/book.py` — canonical mirror reads
- `renewal-desk` · `retention-risk-scout` · `hermes-crm-writer`
