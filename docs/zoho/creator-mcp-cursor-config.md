# Zoho Creator MCP — Cursor desktop vs cloud

L has Zoho MCP configured for the **Creator** app on desktop Cursor.
Cloud agents do **not** inherit that desktop MCP list. This file is the
attach checklist so a cloud run can actually see Creator tools.

Same split as SharePoint: desktop MCP ≠ cloud-environment MCP.

| Place | What it is | Creator MCP? |
|---|---|---|
| Desktop Cursor → Settings → Tools & MCP | Your local MCP list | Yes, if you added Zoho MCP here |
| Cloud Agents environment `googrlc/rsg-hermes` | What this VM loads | **No** as of 2026-08-18 |
| This repo (Hermes `ZOHO_*`) | Zoho **CRM** REST client | CRM only — not Creator |

## What this cloud run actually had

Servers loaded on the "Zoho MCP creator app setup" agent:

| Server | Status | Notes |
|---|---|---|
| `Nowcerts` | ready | AMS |
| `Supabase` | ready | ops DB |
| `MCP-Hermes` | ready | zero tools discovered |
| `cursor-cloud` | ready | run diagnostics |
| `sharepoint` | error | discovery failed |
| `1password` | error | discovery failed |
| **Zoho / Creator** | **absent** | not registered on the environment |

No `mcp_auth_error` event fired for Zoho — the server was never attached, so
auth never ran.

## Attach Creator MCP to cloud agents

Do this on the **Cloud Agents environment**, not only in desktop Cursor.

1. Open the environment: [googrlc/rsg-hermes](https://cursor.com/dashboard/cloud-agents/environments/e/2097123b-99aa-11f1-ba66-0e7d0216e441).
2. In Zoho: **MCP console → Connect → MCP Clients → Cursor**. Copy the JSON
   snippet for the **Creator** server (not CRM Data Insights / Data Operations).
3. Paste that server into the environment's MCP settings. Typical shape:

   ```json
   {
     "mcpServers": {
       "zoho-creator": {
         "url": "https://<org-host>.zohomcp.in/mcp/<id>/message"
       }
     }
   }
   ```

4. Connect / Allow OAuth if Cursor prompts. The agent must run as a user who
   can open the Creator app (Gretchen/Lamar org login — not a shared robot).
5. Relaunch a cloud agent. The MCP catalog must show a Zoho/Creator server as
   `ready` **before** any install playbook runs.

Do **not** commit the `*.zohomcp.in` URL. The path is org-specific.

### Desktop Cursor (already done)

If desktop already works, leave it. Cloud still needs the copy on the
environment. Confirm locally under Settings → Tools & MCP that the Creator
server is connected, then add the **same** JSON to the environment.

## What Creator MCP can and cannot do

Zoho Creator MCP is an action layer on **an existing app** (records, reports,
approvals, environment publish). It is not a full IDE.

**Usually available (names vary by Zoho pack):**

- List applications / workspaces / forms / reports
- Get form fields and report metadata + data
- Add / update / delete records
- Blueprint / approval actions
- Environment lifecycle: stage/production publish, usage

**Usually not available via MCP:**

- Clicking through the Creator IDE
- Creating CRM integration reports from scratch
- Pasting Page HTML and wiring `{{CRM_REPORT_URL}}`
- Authoring Deluge workflows as files

If a tool to create pages/workflows is missing, stop and use the IDE or Zia.
Do not invent schema. Spec source: PR [#353](https://github.com/googrlc/rsg-hermes/pull/353)
(`docs/zoho/creator-renewals-desk/` on `hermes/zoho-creator-renewals-desk`).
Zia one-file pack: PR [#352](https://github.com/googrlc/rsg-hermes/pull/352).

## After it is visible

Follow [`creator-mcp-playbook.md`](creator-mcp-playbook.md). First call is
always "list applications", then write live link names into
[`creator-mcp-inventory.md`](creator-mcp-inventory.md).
