# Microsoft MCP — Cursor vs Hermes-hosted

RSG uses Microsoft 365 in two different MCP shapes. They are **not the same server**.

| MCP | Where it runs | Auth | Covers |
|---|---|---|---|
| **`powerautomate-mcp`** (npm) | Your Mac / Cursor | Delegated (you sign in via `--setup`) | SharePoint, OneDrive, Excel, **Power Automate**, Power Apps, Dataverse, … (~228 tools) |
| **`sharepoint_mcp.py`** (this repo) | hermes-gretch / Cursor stdio | App-only (`MS365_*` client credentials) | SharePoint **read-only** knowledge (7 tools incl. `list_sites`) |

Amy Phase 1 needs **both concepts**, not one replacing the other:

- **Cursor (you building/cleaning up sites)** → use the full **`powerautomate-mcp`** server.
- **Hosted Amy / Copilot connector (unattended)** → use **`deploy/sharepoint_mcp/`** HTTP on `:8082` with service credentials.

---

## What you should see in Cursor (full Microsoft stack)

If you ran the Power Platform MCP setup, Cursor should have **one** server (name varies) backed by:

```bash
npm install -g powerautomate-mcp
powerautomate-mcp --setup --client cursor
powerautomate-mcp --doctor
```

Or without global install:

```bash
npx -y powerautomate-mcp@latest --setup --client cursor
```

Manual `mcpServers` entry (if not using `--setup`):

```json
{
  "mcpServers": {
    "microsoft": {
      "command": "npx",
      "args": ["-y", "powerautomate-mcp@latest"],
      "env": {
        "PA_MCP_CLIENT_ID": "your-entra-app-client-id"
      }
    }
  }
}
```

After restart, you should see tool groups such as:

| Area | Example tools |
|---|---|
| **SharePoint** | `search_sharepoint_sites`, `list_sharepoint_files`, `get_sharepoint_file_content` |
| **OneDrive / Excel** | `search_excel_files`, … |
| **Power Automate** | flow list/create/run/diagnostics (group varies by permission preset) |
| **Power Apps / Dataverse** | connection and table tools (if enabled in setup) |

**Site inventory for consolidation** (required **before** building RSG-Knowledge):

```bash
python scripts/sharepoint_site_inventory.py --deep
```

Or via MCP:

```text
# Hermes SharePoint MCP
list_sites query="*"

# Power Platform MCP (npm)
search_sharepoint_sites query="RSG"
list_sharepoint_files ...
```

Output: [`sharepoint-site-inventory.md`](sharepoint-site-inventory.md) — fill Decision / Target folder, then build the single site.

Docs: [powerplatform-mcp-docs](https://github.com/rcb0727/powerplatform-mcp-docs)

---

## What this cloud agent sees today

In this Cursor **cloud** run, only a server named **`sharepoint`** is registered, pointed at:

```text
python /workspace/sharepoint_mcp.py
```

That is the **Hermes minimal SharePoint server** from PR #348 — not `powerautomate-mcp`. So from here I do **not** see Power Automate, OneDrive, or Power Apps tools unless you add a second MCP entry (or replace the command with `npx powerautomate-mcp`).

Common reasons the npm stack is missing in cloud:

- Config lives on your **desktop Cursor**, not the cloud agent VM.
- Only `sharepoint_mcp.py` was added to the project MCP config.
- The `sharepoint` server fails to start (`python` / missing file / missing env) — tools never load.

---

## Recommended setup for L (desktop Cursor)

Use **two** MCP servers:

```json
{
  "mcpServers": {
    "microsoft": {
      "command": "npx",
      "args": ["-y", "powerautomate-mcp@latest"],
      "env": {
        "PA_MCP_CLIENT_ID": "..."
      }
    },
    "hermes": {
      "command": "npx",
      "args": ["..."],
      "env": { "API_SERVER_KEY": "..." }
    }
  }
}
```

- **`microsoft`** — site cleanup, OneDrive, Power Automate, SharePoint lists/files (interactive, delegated).
- **`hermes`** — agency book, renewals, carriers via `rsg-hermes` MCP bridge (when wired).

Optional third: **`sharepoint-hermes`** only if you want to test the repo’s app-only server locally:

```json
"sharepoint-hermes": {
  "command": "python3",
  "args": ["/path/to/rsg-hermes/sharepoint_mcp.py"],
  "env": {
    "MS365_TENANT_ID": "...",
    "MS365_CLIENT_ID": "...",
    "MS365_CLIENT_SECRET": "...",
    "SHAREPOINT_SITE_URL": "https://tenant.sharepoint.com/sites/RSG-Knowledge"
  }
}
```

Do **not** point the same server name at both `sharepoint_mcp.py` and `powerautomate-mcp`.

**Full tenant checklist:** [`microsoft-tenant-mcp-setup.md`](microsoft-tenant-mcp-setup.md)

---

## Hermes-hosted SharePoint (production / Amy)

For unattended Amy on hermes-gretch:

- [`deploy/sharepoint_mcp/README.md`](../deploy/sharepoint_mcp/README.md)
- Tools: `list_sites`, `search_knowledge`, `read_document`, …
- Env: `MS365_*` + `SHAREPOINT_SITE_URL` + optional `API_SERVER_KEY`

Power Automate **glue flows** (scheduled sync) stay in Power Automate Studio — not in the Hermes SharePoint MCP. See [`rsg-digital-operating-system.md`](rsg-digital-operating-system.md).

---

## Consolidation workflow (which MCP to use)

| Task | Use |
|---|---|
| Inventory all SharePoint sites | `microsoft` → `search_sharepoint_sites` **or** `sharepoint-hermes` → `list_sites` |
| Browse/move files during cleanup | `microsoft` (write-capable with delegated auth) |
| Publish single **RSG-Knowledge** site for Amy | SharePoint UI + [`sharepoint-knowledge-consolidation.md`](sharepoint-knowledge-consolidation.md) |
| Copilot / hosted Amy read-only Q&A | Hermes HTTP SharePoint MCP + `SHAREPOINT_SITE_URL` |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Only SharePoint tools, no Power Automate | You are on `sharepoint_mcp.py` — switch to or add `powerautomate-mcp` |
| MCP server error / 0 tools | Run `powerautomate-mcp --doctor` or fix `MS365_*` on Hermes server |
| Cloud agent cannot see your desktop MCP | Expected — configure MCP in **Cursor Settings** on the machine you use, or add env to cloud environment |
| Duplicate SharePoint tools from two servers | Disable one; prefer `microsoft` for cleanup, `sharepoint-hermes` for app-only tests |
