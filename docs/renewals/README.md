# Renewals — Hermes executor + Perplexity cockpit

Two documents that split renewal work between Gretchen's Perplexity Space (read +
draft) and Hermes (the one door that writes). See the AMS/CRM Access Contract for the
governing rules; these sit under it.

| File | Runs where | Purpose |
|---|---|---|
| [`renewal-desk-skill.md`](./renewal-desk-skill.md) | **Hermes** (Claude Code) | The executor. Receives a renewal request from Gretchen and performs the sanctioned, additive, queued, approved AMS/CRM writes. |
| [`perplexity-space-playbook.md`](./perplexity-space-playbook.md) | **Perplexity Space** | Gretchen + Perplexity Computer: read renewals, prep packets, draft outreach, and hand execution to Hermes via `@Hermes RENEWAL ACTION`. Read/draft only — never writes. |

## Where the live copies live

These are the **version-controlled sources**. The functional copies are:

- **Skill** → `~/.claude/skills/renewal-desk/SKILL.md` (auto-loads for Hermes; must stay
  in the skills dir to trigger). Keep it in sync with `renewal-desk-skill.md` here.
- **Playbook** → paste `perplexity-space-playbook.md` into the Perplexity Space's custom
  instructions, **below the AMS/CRM Access Contract**.

## Deploy order (Perplexity Space)

1. AMS/CRM Access Contract (hard rules) — already pasted.
2. `perplexity-space-playbook.md` (renewals operating procedure) — paste under it.

## Capability notes (2026-07-10)

- espocrm MCP write tools: `create_note`, `create_task`, `update_task`,
  `create_opportunity`, `update_opportunity` (server `mcp/espo/src/server.js`, 15 tools).
- Espo **Task create requires an assignee** — pass `assignedUserId` (Gretchen for PL,
  Lamar `69bdad92458da2204`) or set `ESPO_DEFAULT_TASK_ASSIGNEE_ID` in the env.
- **Cases are not yet tooled** — no Case read/write tool exists; capture as a Task or
  flag Lamar until one is added.
