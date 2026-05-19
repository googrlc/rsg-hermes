---
name: espocrm-developer
description: Developer skill for building, debugging, and extending Hermes' EspoCRM integration — REST client code, MCP sidecar (`mcp/espo/`), sync pipeline, queue workers, schema registry, and field-mapping migrations. Use when writing or modifying Python that touches EspoCRM, adding MCP tools, debugging sync errors, or aligning field names.
---

# EspoCRM Developer Skill

For engineering work on Hermes ↔ EspoCRM integration. Pair this with
`espocrm-field-reference` when you need entity/field knowledge.

## When to use

- Writing or modifying code under `hermes/integrations/`, `hermes/sync/`,
  `hermes/operations/crm_queue_worker.py`, or `mcp/espo/`.
- Adding an MCP tool to the EspoCRM sidecar.
- Debugging `audit_log` or `sync_errors` rows in Supabase.
- Migrating a field from camelCase → snake_case in handlers.
- Aligning Hermes commands with `rsg-espocrm` `entityDefs/*.json` changes.

## Repo map

| Path | Role |
|------|------|
| `hermes/integrations/supabase_client.py` | Supabase access used by sync + audit log |
| `hermes/sync/pipeline.py` | Forward sync orchestration |
| `hermes/sync/bidirectional.py` | Two-way sync logic |
| `hermes/sync/field_mapper.py` | camelCase ↔ snake_case mapping, SchemaRegistry |
| `hermes/operations/crm_queue_worker.py` | Async CRM mutation queue worker |
| `mcp/espo/` | Read-only EspoCRM MCP sidecar (Node/TS); see `mcp/espo/README.md` |
| `hermes-training/espocrm/` | Field reference docs (load via `espocrm-field-reference` skill) |
| `docs/SYNC_FLOW_CONTRACT.md` | Cross-system sync contract |
| `docs/espocrm.md` | Pointer to `rsg-espocrm` for entity defs / hooks |
| `docs/bidirectional-sync-plan.md` | Bidirectional sync design |

Espo entity defs and hooks live in a separate repo:
**https://github.com/googrlc/rsg-espocrm** — don't vendor them here.

## Environment

- `ESPO_URL` — base URL of the EspoCRM instance
- `ESPO_API_KEY` — API key for the Hermes service account
- `ESPO_MCP_BEARER_TOKEN` — bearer for the MCP sidecar
- `ESPO_MCP_PORT` (default 3000), `ESPO_MCP_MAX_LIST_SIZE` (default 200)
- Sidecar reachable inside the `hermes-shared` Docker network at
  `http://rsg-espo-mcp:3000/mcp`.

`docker-compose.yml` runs `hermes`, `hermes-api`, `hermes-crm-queue-worker`,
and the n8n service on the `hermes-shared` network.

## Working rules

1. **Schema first.** Before adding any new field reference, call
   `SchemaRegistry.find_field()` or hit the MCP metadata tool. Never
   hardcode a field name without verifying it exists in the live
   `entityDefs`.
2. **Respect the casing migration.** All *new* fields must be `snake_case`.
   Existing camelCase fields stay until explicitly migrated. Use the field
   mapper rather than ad-hoc string transforms.
3. **List calls are capped.** Honor `MAX_LIST_SIZE = 200`. Page through
   larger result sets.
4. **Writes go through the queue.** Mutations should enqueue via the CRM
   queue worker, not call the REST API inline from a request handler,
   unless the contract in `docs/SYNC_FLOW_CONTRACT.md` says otherwise.
5. **Audit + error tables are the live contract.** When changing sync code,
   re-verify columns against the Supabase tables — see
   `hermes/sync/pipeline.py` and `1f8ff09 fix(sync): align audit_log and
   sync_errors with live Supabase schema` for the pattern.
6. **MCP sidecar is read-only by design.** Add writes only via the Hermes
   API and the queue worker.

## Adding an MCP tool

1. Implement the tool in `mcp/espo/src/` and register it in the server entry.
2. Add the tool name to `mcp/espo/README.md`.
3. Document any new env vars in the README and `docker-compose.yml`.
4. If the tool touches a new field, also update
   `hermes-training/espocrm/field_dictionary.md`.

## Tests

- `tests/test_sync_pipeline.py`
- `tests/test_bidirectional_sync.py`
- `tests/test_crm_queue_worker.py`
- `tests/test_crm_readiness.py`
- `tests/test_data_quality.py`
- `tests/test_nl_agent.py`

Run with `poetry run pytest` (or the project's configured runner). When you
add a sync field or MCP tool, add a test case covering both the happy path
and a sync-error row in Supabase.

## Common pitfalls

- Treating `lineOfBusiness` (Opportunity) and `line_of_business` (Policy /
  Renewal) as the same field. They share a vocabulary but live under
  different names — use the field mapper.
- Forgetting that Contact ↔ Account is many-to-many. Don't pick the first
  account silently.
- Querying a polymorphic parent (`Task.parent`) without filtering on
  `parentType`.
- Skipping a stage in the Opportunity or Renewal pipeline (see
  `workflows.md` for the canonical progression).
