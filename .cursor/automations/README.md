# Cursor Automations (RSG Hermes)

Repo-defined automation specs for [Cursor Automations](https://cursor.com/docs/cloud-agent/automations).
Use these when creating automations at [cursor.com/automations/new](https://cursor.com/automations/new).

**Results stay in Cursor** — each run produces a report in the agent thread at
`cursor.com/agents`. No Slack, no Copilot, no email.

## Data Quality Investigator

| File | Purpose |
|---|---|
| [`data-quality-investigator.md`](data-quality-investigator.md) | Single-policy AMS vs mirror vs renewal investigation |

### One-time setup (~5 minutes)

1. Open **[cursor.com/automations/new](https://cursor.com/automations/new)** (or Agents Window → Automations → New).

2. **Name:** `RSG Data Quality Investigator`

3. **Trigger:** pick one:
   - **Manual** — you paste a case in Cursor Agents when you need it
   - **Webhook** — scripts or other systems POST a case; the run still appears in Cursor

4. **Repository:** `googrlc/rsg-hermes`  
   - **Environment:** `rsg-hermes`  
   - **Branch:** `main` (after PR #351 merges)

5. **Tools to enable:**
   - **MCP** — Supabase, Hermes MCP bridge (`investigate_policy`, `book_sync_health`)
   - **ZohoMCP** (optional)
   - **Do not enable** Send to Slack — results are read in the Cursor run
   - Disable **Open pull request** — read-only

6. **Prompt:** copy everything under `## Instructions` from
   [`data-quality-investigator.md`](data-quality-investigator.md).

7. **Save & activate.**

### How you get results

| How you start it | Where the answer appears |
|---|---|
| Run automation manually in Cursor | This agent thread |
| Webhook POST (see below) | New run at `cursor.com/agents/<run-id>` — open from Automations dashboard or email notification if enabled |
| `@` the agent in Cursor with a policy case | This chat |

### Invoke via webhook (optional)

```bash
export CURSOR_AUTOMATION_WEBHOOK_URL="..."   # from automation trigger panel
export CURSOR_AUTOMATION_WEBHOOK_KEY="..."

./scripts/trigger_policy_investigation.sh 990414352 "Steven Prak" "Personal Auto"
```

Plain-text body also works: `990414352, Steven Prak, Auto`

Open the run in Cursor to read the full investigation report.

### Invoke in Cursor (typical)

Run the saved automation or message:

```
Investigate policy 990414352 for Steven Prak, Auto
```

### MCP prerequisites

Authenticate in **Cursor Settings → MCP** before cloud runs:

| Server | Required | Purpose |
|---|---|---|
| Supabase | Yes | Mirror + renewal tables (fallback) |
| Hermes MCP bridge | Yes | `investigate_policy` |
| ZohoMCP | No | CRM cross-check |

Hermes API: `GET /api/hermes/investigate-policy` (PR #351).

### Approvals

Corrections are **not** auto-applied. Reply in the same Cursor thread:

```
APPROVE BOOK SYNC
APPROVE RENEWAL REFRESH
```

—or start a new agent run with those tokens after reviewing the report.
