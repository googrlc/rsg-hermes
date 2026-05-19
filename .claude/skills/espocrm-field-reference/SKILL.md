---
name: espocrm-field-reference
description: Authoritative reference for RSG's EspoCRM entities, fields, relationships, and query patterns. Use when constructing CRM queries, mapping natural-language requests to entities/fields, validating field names (camelCase vs snake_case migration), or answering "what does X mean in the CRM."
---

# EspoCRM Field Reference

RSG-specific EspoCRM schema knowledge for any agent that reads from or writes
to the live CRM via Hermes, the MCP sidecar, or the REST API directly.

## When to use this skill

Trigger this skill when the user (or another agent) asks about:

- CRM entities: Account, Contact, Lead, Opportunity, Policy, Renewal,
  Commission, Task, ActivityLog, Quote, OpportunityDriver,
  OpportunityVehicle, ClientNote.
- Field names, types, enum values, or naming convention (camelCase vs
  snake_case).
- Entity relationships (e.g. "which entity links Policy to Commission?").
- Mapping natural-language phrasing ("show me at-risk renewals this quarter")
  to CRM queries.
- Stage pipelines for Opportunity or Renewal.
- The CRM glossary (Client, Prospect, LOB, FEIN, X-date, MGA, Carrier).
- Read/write guardrails before executing a CRM mutation.

## Reference files

The source-of-truth content lives in `hermes-training/espocrm/`. Load the
files you need on demand — they are concatenable as a single system prompt
in the order below.

| File | When to load |
|------|--------------|
| `hermes-training/espocrm/schema.md` | Entity overview, key fields, relationships at a glance |
| `hermes-training/espocrm/field_dictionary.md` | Per-entity field inventory with types and enum values |
| `hermes-training/espocrm/relationships.md` | Entity relationship graph (ASCII tree + link table) |
| `hermes-training/espocrm/query_patterns.md` | NL → CRM query examples |
| `hermes-training/espocrm/workflows.md` | MCP operating instructions, CRM glossary, env vars |
| `hermes-training/espocrm/guardrails.md` | Read/write safety, severity vocab, Slack routing |

## Hard rules

1. **Never assume field names.** The codebase is migrating camelCase →
   snake_case. Verify via `SchemaRegistry.find_field()` or the MCP
   `get_crm_record` / metadata tool before constructing a query.
2. **Cap list calls at `MAX_LIST_SIZE` (200).** Page if you need more.
3. **Walk relationships, don't guess.** A Contact may belong to multiple
   Accounts; verify the link before assuming ownership.
4. **Never mutate without explicit user intent.** Summarize the proposed
   change before executing any write. See `guardrails.md` for full rules.

## Authoritative source

When the docs and live CRM disagree, the live EspoCRM metadata API
(`GET /api/v1/Metadata`) wins. Update `hermes-training/espocrm/` and the
custom-fields CSV when entity defs change in `rsg-espocrm`.
