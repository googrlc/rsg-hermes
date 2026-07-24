---
name: crm-upsert-planner
description: Convert an approved CRM intake payload into a concrete, ordered upsert plan — duplicate searches first, then create-or-update decisions for Account → Contacts → Opportunities → Note → Facts, with the exact n8n webhook or `crm_write_queue` rows that should run for each step. Use after `crm-intake-writer` produces a draft and the user returns an approval token. Also use when asked "what would actually happen if I approve this?" or "show me the write plan."
---

# CRM Upsert Planner

The bridge between a drafted intake payload and the actual write path. This
skill emits a deterministic, auditable plan — no narrative, no surprises.

## When to use

- An approval token (`APPROVE ALL`, `APPROVE CRM ONLY`, etc.) was returned
  for a payload produced by `crm-intake-writer`.
- User asks "show me the write plan" / "what will this do?" / "preview the
  upserts."
- A specialty intake skill needs to translate its draft into queue rows.

## Decision vocabulary

For each entity, the planner emits exactly one of:

- `CREATE_NEW`
- `UPDATE_EXISTING` (with target ID)
- `ATTACH_ONLY` (no field changes, only relationship adds)
- `NEEDS_HUMAN_REVIEW` (possible duplicate, conflicting fields)
- `SKIP` (entity already exists and matches all fields)

## Plan ordering — strict

1. **Account first.** Everything else FK's to it.
2. **Contacts second.** Linked to the Account.
3. **Opportunities third.** One per LOB. Linked to Account + primary contact.
4. **Note fourth.** Linked to Account (+ optionally to specific Opportunity).
5. **Facts last.** Written to `client_facts` after CRM IDs exist so they can
   reference `crm_account_id` / `crm_contact_id` / `crm_opportunity_id`.

Within each step, run **all searches before any writes**. A search that
returns a duplicate must short-circuit to `NEEDS_HUMAN_REVIEW` instead of
creating.

## Output shape

```json
{
  "plan_id": "crm-upsert-<ulid>",
  "source_payload_ref": "intake draft id or message_ts",
  "approval_token": "APPROVE ALL",
  "approved_by": "Lamar Coates",
  "approved_at": "2026-05-19T14:22:00-04:00",

  "steps": [
    {
      "order": 1,
      "phase": "search",
      "entity": "Account",
      "queries": [
        {"by": "fein", "value": "33-3725730"},
        {"by": "name", "value": "3D Pumps LLC"},
        {"by": "name", "value": "3D Pumps"},
        {"by": "address", "value": "503 S Evelyn Pl NW, Atlanta, GA 30318"}
      ],
      "transport": {
        "type": "n8n_webhook",
        "path": "/search-account"
      }
    },
    {
      "order": 2,
      "phase": "write",
      "entity": "Account",
      "decision": "CREATE_NEW",
      "reason": "No FEIN match, no name match, no address match.",
      "payload": { "...": "the CRM Account fields" },
      "transport": {
        "type": "crm_write_queue",
        "target_system": "the CRM",
        "entity_type": "Account",
        "operation": "POST"
      },
      "expected_receipt": {
        "fields": ["id", "name", "fein"]
      }
    },
    {
      "order": 3,
      "phase": "search",
      "entity": "Contact",
      "queries": [
        {"by": "email", "value": "jarod.mattison@gmail.com"},
        {"by": "phone", "value": "+17707808848"},
        {"by": "name", "value": "Jarod Mattison"}
      ]
    },
    {
      "order": 4,
      "phase": "write",
      "entity": "Contact",
      "decision": "CREATE_NEW",
      "payload": { "...": "..." },
      "links": [{"to_entity": "Account", "from_step": 2}]
    },
    {
      "order": 5,
      "phase": "search",
      "entity": "Opportunity",
      "queries": [
        {"by": "name_exact", "value": "3D Pumps LLC - General Liability - 05/19/2026"},
        {"by": "account_lob_date", "values": ["3D Pumps LLC", "General Liability", "2026-05-19"]},
        {"by": "quote_number", "value": "656137"}
      ]
    },
    {
      "order": 6,
      "phase": "write",
      "entity": "Opportunity",
      "line_of_business": "General Liability",
      "decision": "CREATE_NEW",
      "payload": { "...": "..." },
      "links": [
        {"to_entity": "Account", "from_step": 2},
        {"to_entity": "Contact", "from_step": 4, "role": "primary_contact"}
      ]
    },
    {
      "order": "...",
      "phase": "write",
      "entity": "Opportunity",
      "line_of_business": "Workers Compensation",
      "decision": "CREATE_NEW",
      "...": "one step per LOB"
    },
    {
      "order": "N-2",
      "phase": "write",
      "entity": "ClientNote",
      "decision": "CREATE_NEW",
      "links": [{"to_entity": "Account", "from_step": 2}]
    },
    {
      "order": "N-1",
      "phase": "write",
      "entity": "client_facts",
      "decision": "CREATE_NEW",
      "rows": [
        {
          "entity_type": "Account",
          "crm_account_id_from_step": 2,
          "fact_label": "EIN",
          "fact_value": "33-3725730",
          "sensitivity": "restricted",
          "source": "underwriting summary",
          "confidence": "high"
        }
      ],
      "transport": {
        "type": "supabase_insert",
        "table": "client_facts"
      }
    }
  ],

  "rollback": {
    "strategy": "queue-receipt-based",
    "instructions": "Each write step records a crm_receipts row with the transaction_id. To roll back, mark the receipt as REVERTED and enqueue a compensating DELETE/PATCH in reverse step order. Do not delete Account if Contacts or Opportunities reference it — unlink first."
  },

  "guardrail_checks": [
    "All LOBs have a single Opportunity row (no bundles).",
    "No duplicate Account by FEIN/name/address.",
    "No duplicate Contact by email/phone.",
    "Approval token recognized.",
    "Sensitive facts marked restricted.",
    "All Opportunity stages are in the canonical enum."
  ]
}
```

## Transport options

The planner emits transport hints but does not execute. Allowed targets:

- `n8n_webhook` — `/search-account`, `/search-contact`,
  `/search-opportunity`, `/upsert-account`, `/upsert-contact`,
  `/upsert-opportunity`, `/create-note`, `/create-fact`, `/search-facts`.
- `crm_write_queue` — Supabase queue row (`target_system="the CRM"`,
  `entity_type`, `payload`, `created_by_role`). Worker dequeues and POSTs.
- `supabase_insert` — direct insert into the retrieval tables
  (`client_facts`, `client_notes`, `client_documents`, `quote_facts`,
  `policy_facts`) once the CRM IDs exist.

The actual choice depends on what's wired in `docker-compose.yml` and the
n8n workflow inventory. When in doubt, default to `crm_write_queue` for
CRM mutations and `supabase_insert` for retrieval rows.

## Hard rules

1. **Search exhaustively before any write.** Every entity gets its
   `phase: "search"` step first.
2. **No duplicate creates.** If a confident match returns, the step is
   `UPDATE_EXISTING` or `NEEDS_HUMAN_REVIEW`. Never `CREATE_NEW` on a
   match.
3. **One Opportunity per LOB.** Bundling is forbidden.
4. **Honor the approval token scope.**
   - `APPROVE CRM ONLY` → emit only CRM steps; skip fact rows.
   - `APPROVE SUPABASE ONLY` → emit only fact/note retrieval rows.
   - `APPROVE TASKS ONLY` → emit only Task creation steps.
   - `APPROVE ALL` → emit everything.
   - `REVISE` → return to drafting; emit no plan.
   - `CANCEL` → emit `{"plan_id": "...", "steps": [], "status": "CANCELED"}`.
5. **Respect AMS locks.** Policies with `amsLockState = "Synced"` get
   `NEEDS_HUMAN_REVIEW`, not `UPDATE_EXISTING`.
6. **Never invent IDs.** Reference IDs by `from_step` rather than guessing
   a UUID. The worker fills them in from receipts.
7. **Stage enum only.** Opportunity / Renewal stages must come from the

## Post-write confirmation

After the worker executes, render a confirmation summary back to the user:

```
Wrote to CRM:
  Account:       3D Pumps LLC (id: 64f...) — CREATED
  Contacts:      Jarod Denero Mattison (id: 71a...) — CREATED
  Opportunities: 6 created
                 - GL  (id: 88c...) Quoting
                 - WC  (id: 88d...) Discovery
                 - Auto (id: 88e...) Discovery
                 - IM   (id: 88f...) Quoting
                 - CPL  (id: 88g...) Discovery
                 - UMB  (id: 88h...) Discovery
  Note:          Underwriting Summary (id: 9aa...) — CREATED
  Facts staged:  9 rows in client_facts

Receipts: 11 logged in crm_receipts.
Guardrail events: 0.
```

If anything failed (`BLOCKED_BY_GUARDRAIL` / `FAILED`), surface the failed
step IDs and the receipt error so a human can intervene.

## References

- `docs/agency-memory-plan.md`
- `docs/hermes-operating-constitution.md` — queue + receipt contract
- `hermes/operations/crm_queue_worker.py` — the worker that consumes queue rows
- `crm-intake-writer` — produces the input payload
- `crm-fact-retriever` — reads the facts this planner stages
