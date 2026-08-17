# Amy — Copilot Knowledge setup (SharePoint native)

Wire Amy to agency SOPs and playbooks **without** Hermes or SharePoint MCP.
Microsoft indexes SharePoint in the cloud; the hermes-gretch box is not in this path.

Related: [`sharepoint-migration-status.md`](sharepoint-migration-status.md) ·
[`amy-getting-started.md`](amy-getting-started.md) ·
[`amy-copilot-tool-wiring.md`](amy-copilot-tool-wiring.md)

---

## What you are connecting

| Item | Value |
|---|---|
| **Site** | RSG |
| **Host** | `riskintranet.sharepoint.com` |
| **Site path** | `/sites/RSG` |
| **Browser home** | `…/SitePages/Home.aspx` — select the **site**, not this page |
| **Scope** | This site **only** — not “all SharePoint” |

Hermes MCP (`hermes-mcp…`) handles book, renewals, sync, commissions.  
This knowledge source handles **how we work** (SOPs, templates, training).

---

## Prerequisites

- Copilot Studio license (or M365 Copilot / Copilot Studio trial) for the RSG tenant
- You can open the RSG site in a browser while signed in as Lamar (or whoever owns the agent)
- Vault content migrated into the site’s **Documents** library (folders like `01-operations/`, `02-personal-lines/`, etc.)
- Prefer **Markdown** SOPs in SharePoint — Copilot retrieves text better than scanned PDFs alone

---

## Step 1 — Open the Amy agent

1. Go to [Copilot Studio](https://copilotstudio.microsoft.com) (or Power Platform → Copilot Studio).
2. Select the **RSG environment** (same tenant as `riskintranet`).
3. Open the **Amy** agent (or create one: single assistant, RSG persona).

---

## Step 2 — Add SharePoint knowledge

UI labels vary slightly by Copilot Studio version. Look for one of:

- **Knowledge** → **Add knowledge**
- **Generative AI** → **Knowledge sources** → **Add**
- **Settings** → **Knowledge**

Then:

1. Choose **SharePoint** (or **SharePoint site**).
2. Sign in / confirm the same work account if prompted.
3. Search for **`RSG`** or browse to site **RSG** on host `riskintranet.sharepoint.com`.
4. Select the **RSG** site — **not** the whole tenant, not every site collection.
5. If the UI offers scope:
   - **Include:** site **RSG**, library **Documents** (and site pages if you use them for SOPs)
   - **Exclude:** other sites, personal OneDrive, unrelated team sites
6. Save / **Sync** / **Publish** (wording varies).

**Do not** add Hermes MCP here — that is a separate **Actions** / **Connectors** step.

---

## Step 3 — Enable generative answers from knowledge

In the agent’s **Generative AI** (or **Overview**) settings:

| Setting | Recommendation |
|---|---|
| **Use generative answers** | On |
| **Knowledge sources** | SharePoint **RSG** only (for Phase 1) |
| **Content moderation** | RSG defaults / existing Amy guardrails |
| **Fallback** | “I don’t have that in our knowledge base” — don’t invent carrier or policy facts |

Add a short instruction in the agent **Instructions** / **System message**:

```text
For procedures, SOPs, templates, and internal how-to questions, answer from
SharePoint site RSG (riskintranet) first. Do not guess policy or client data —
use Hermes tools for book, renewals, and AMS data.
```

---

## Step 4 — Publish

1. **Test** in the Copilot Studio test pane (right side).
2. **Publish** the agent (Teams / M365 Copilot / channel you use for staff).
3. Wait for indexing — new or large uploads can take **15–60 minutes** (sometimes longer).

---

## Step 5 — Smoke tests (knowledge only)

Ask Amy in the test pane:

| Prompt | Pass criteria |
|---|---|
| “Where is the COI procedure?” | Cites SharePoint / RSG content (service-desk or personal-lines SOP) |
| “What folders hold personal lines reference material?” | Mentions migrated folder structure if `site-index.md` or folders exist |
| “What is Bull Dawg Trucking’s renewal premium?” | Should **not** answer from SharePoint — should say it needs book/Hermes data |

If procedure questions fail but you can read the SOP in SharePoint in a browser:

- Confirm the file is in **Documents**, not only on someone’s OneDrive
- Confirm `.md` / `.docx` with real text (not empty or image-only PDF)
- Re-save knowledge source and wait for re-index

---

## Step 6 — Hermes MCP (separate, already live)

Keep the existing connector:

| Connector | URL |
|---|---|
| Hermes | `https://hermes-mcp.risksolutionsgroup.net/mcp` |
| Auth | `X-API-Key` or Bearer = `API_SERVER_KEY` |

Do **not** add SharePoint MCP (`sharepoint-mcp…`) unless you need explicit browse/search tools — native knowledge covers Phase 1.

---

## Troubleshooting

| Symptom | Likely fix |
|---|---|
| Site not in picker | Open the RSG site in browser on `riskintranet`; confirm you have access; search “RSG” |
| Generic answers, no citations | Indexing not finished; re-publish knowledge; check Generative AI is on |
| Wrong site content | Remove other SharePoint sources; only **RSG** for Phase 1 |
| “I can’t access SharePoint” | Agent owner / connection account needs read access to RSG site |
| Procedures OK, book data wrong | Expected — wire Hermes tools for AMS/Supabase; don’t ground book data in SharePoint |
| **“Blocked by your organization’s data loss prevention policy”** | Power Platform **DLP** — see below |

### DLP policy block (not a Hermes / SharePoint site bug)

Microsoft enforces **Data Loss Prevention** on Copilot Studio agents (tenant-wide since 2025).
If adding SharePoint knowledge fails with a data policy violation, **IT must change Power
Platform DLP** — you cannot fix this on hermes-gretch.

**Who:** Power Platform admin or Global admin (environment admins may not see tenant policies).

**Where:** [Power Platform admin center](https://admin.powerplatform.com) → **Policies** →
**Data policies** → check policies scoped to **All environments** (tenant-level wins over
environment-level).

**Connectors to allow (Business group or explicit allow):**

- **Knowledge source with SharePoint and OneDrive in Copilot Studio**
- Optionally: **Knowledge source with documents in Copilot Studio**

Microsoft docs: [Configure data policies for agents](https://learn.microsoft.com/en-us/microsoft-copilot-studio/admin-data-loss-prevention),
[DLP troubleshooting](https://learn.microsoft.com/en-us/microsoft-copilot-studio/admin-dlp-troubleshooting).

**Least-privilege alternative:** instead of allowing all SharePoint, use **connector endpoint
filtering** on that knowledge connector to permit only:

- Host: `riskintranet.sharepoint.com`
- Path prefix: `/sites/RSG`

**In Copilot Studio:** use **Details** / download the error report on the agent — it lists
the **DLP policy name** and **policy id** to hand to IT.

**If DLP cannot change soon:** upload critical SOPs via **Documents** knowledge source (if
that connector is allowed), or finish Entra `Sites.Read.All` for Hermes SharePoint MCP on
the box (separate path — still needs admin consent, not DLP in PPAC).

---

## Done checklist

- [ ] SharePoint knowledge source = **RSG** only (`riskintranet` tenant, path `/sites/RSG`)
- [ ] Generative answers enabled with Amy guardrails
- [ ] COI / service-desk smoke question passes with SharePoint-backed answer
- [ ] Hermes MCP connector still works (`ping` / renewals / sync_health)
- [ ] SharePoint MCP container on hermes-gretch **optional** — can stay stopped
