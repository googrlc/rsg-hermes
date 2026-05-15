# EspoCRM Training Docs for Hermes

> Reference knowledge for any AI agent — Hermes NL agent, MCP server, Slack
> bot, or external copilot — that interacts with RSG's EspoCRM instance.
>
> Load these documents as system/profile instructions before the first CRM
> interaction in a session. They contain **no secrets** and are safe to
> version-control and share with connected tools.

## File Index

| File | Purpose |
|------|---------|
| [`schema.md`](schema.md) | Core and supporting entity definitions with key fields |
| [`field_dictionary.md`](field_dictionary.md) | Detailed field inventory per entity — types, enums, and naming conventions |
| [`relationships.md`](relationships.md) | Entity relationship graph (ASCII tree + link table) |
| [`query_patterns.md`](query_patterns.md) | Lookup, report, and traversal examples mapping natural language → CRM queries |
| [`workflows.md`](workflows.md) | MCP operating instructions, CRM glossary, Supabase domain context, environment variables |
| [`guardrails.md`](guardrails.md) | Read/write safety, data integrity, Slack routing, and severity vocabulary |

## How to Use

**As a system prompt:** Concatenate the files in order (schema → relationships
→ workflows → guardrails) and inject as the system message for an LLM session.

**As MCP context:** Point the MCP server's `--profile` or context-injection
config at this directory. Each file is self-contained and can be loaded
individually.

**As a Skill:** Reference these files from a `.agents/skills/` SKILL.md so
future Devin sessions auto-load CRM context when working on Hermes.

## Source of Truth

Field definitions in these docs were extracted from the live EspoCRM
`entityDefs` JSON (in `rsg-espocrm`) and the bundled
`hermes/data/custom-fields-camelcase-audit.csv`. When in doubt, the live
EspoCRM metadata API (`GET /api/v1/Metadata`) is the authoritative source.

## Maintenance

When entity definitions change in EspoCRM:
1. Update the corresponding `entityDefs/*.json` in `rsg-espocrm`
2. Re-export `custom-fields-camelcase-audit.csv` if custom fields changed
3. Update the relevant files in this directory to stay in sync
