# Cursor Automations (RSG Hermes)

Repo-defined automation specs for [Cursor Automations](https://cursor.com/docs/cloud-agent/automations).
Cursor does not auto-import these files yet — use them as the **source of truth** when creating
automations at [cursor.com/automations/new](https://cursor.com/automations/new).

## Data Quality Investigator

| File | Purpose |
|---|---|
| [`data-quality-investigator.md`](data-quality-investigator.md) | Single-policy AMS vs mirror vs renewal investigation |

### One-time setup (5 minutes)

1. Open **[cursor.com/automations/new](https://cursor.com/automations/new)** (or Agents Window → Automations → New).

2. **Name:** `RSG Data Quality Investigator`

3. **Trigger:** **Webhook** (recommended)  
   - After save, copy the webhook URL + API key.  
   - POST policy cases from Slack workflows, n8n, or ops scripts.

4. **Repository:** `googrlc/rsg-hermes`  
   - **Environment:** `rsg-hermes` (the Cloud Agent environment with Supabase + Hermes creds).  
   - **Branch:** `main` (or your deploy branch after PR #351 merges).

5. **Tools to enable:**
   - **MCP server** — Supabase, Hermes MCP bridge (`investigate_policy`, `book_sync_health`), ZohoMCP (optional)
   - **Send to Slack** (optional) — `#ops` or `#hermes-alerts` for investigation summaries
   - **Memories** (optional) — remember recurring stale-mirror patterns
   - Disable **Open pull request** — this automation is read-only

6. **Model:** Any capable model (Composer or Claude Sonnet recommended).

7. **Permissions:** **Team Visible** (ops can see runs; L owns management) or **Team Owned** if using the team service account for MCP.

8. **Prompt:** Copy everything under `## Instructions` from
   [`data-quality-investigator.md`](data-quality-investigator.md) into the automation prompt field.

9. **Save & activate.**

### Invoke via webhook

```bash
curl -X POST "$WEBHOOK_URL" \
  -H "Authorization: Bearer $WEBHOOK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "policy_number": "990414352",
    "client_name": "Steven Prak",
    "line_of_business": "Personal Auto"
  }'
```

Plain-text body also works:

```
990414352, Steven Prak, Auto
```

### Invoke manually

In Cursor Agents, run the saved automation or paste:

```
Investigate policy 990414352 for Steven Prak, Personal Auto.
```

### MCP prerequisites

Authenticate these MCP servers in **Cursor Settings → MCP** before the automation runs:

| Server | Required | Purpose |
|---|---|---|
| Supabase | Yes | Mirror + renewal tables (fallback if Hermes API down) |
| Hermes MCP bridge | Yes | `investigate_policy` one-shot diff |
| ZohoMCP | No | CRM policy/deal status cross-check |
| 1Password | No | Env bootstrap only |

Hermes API must expose `GET /api/hermes/investigate-policy` (shipped in PR #351).

### Slack shortcut (optional)

Create a second automation or n8n flow: when a message in `#ops` matches
`investigate <policy>` or contains a policy number + client name, POST to the webhook.
