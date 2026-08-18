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

3. **Trigger:** **Manual** for ad-hoc runs; **Webhook** for Zoho (see below)
   - Manual: paste a case when you run the automation
   - Webhook: Zoho CRM → Deluge → Cursor — full guide:
     [`docs/integrations/zoho-data-quality-investigator-webhook.md`](../../docs/integrations/zoho-data-quality-investigator-webhook.md)

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

### How you get results (manual trigger)

| Step | Action |
|---|---|
| 1 | Open [cursor.com/agents](https://cursor.com/agents) or **Cursor → Agents** |
| 2 | **Automations** → **RSG Data Quality Investigator** → **Run** |
| 3 | Paste a policy case (see formats below) |
| 4 | Read the report in that run's thread when it finishes |

**Example input:**
```
Investigate policy 990414352 for Steven Prak, Personal Auto
```

Policy number is required. Client name and LOB are optional but help with duplicates.

### Invoke from Zoho CRM (webhook)

See **[`docs/integrations/zoho-data-quality-investigator-webhook.md`](../../docs/integrations/zoho-data-quality-investigator-webhook.md)** for:

1. Enable **Webhook** trigger on the Cursor automation; copy URL + API key
2. Store secrets in Zoho **CRM Variables** (`cursor_dqi_webhook_url`, `cursor_dqi_webhook_key`)
3. Create Deluge function from `docs/zoho/deluge/trigger_policy_investigation.deluge`
4. Workflow rule on **Renewals** (button: “Investigate data quality”) → call function

### Invoke via webhook (shell)

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
