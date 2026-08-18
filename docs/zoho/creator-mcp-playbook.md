# Playbook — use Zoho Creator MCP (once attached)

Run this only when a Zoho/Creator MCP server is `ready` in the cloud agent
catalog. If it is missing, stop and follow
[`creator-mcp-cursor-config.md`](creator-mcp-cursor-config.md).

Target app: **Renewals Desk**. CRM remains the record store. Hermes remains
the only NowCerts writer. Creator is the desk UI.

Spec (PR #353 branch `hermes/zoho-creator-renewals-desk`):

| Piece | Path |
|---|---|
| Install (IDE fallback) | `docs/zoho/creator-renewals-desk/INSTALL.md` |
| Pages | `docs/zoho/creator-renewals-desk/pages/` |
| Reports | `docs/zoho/creator-renewals-desk/reports.md` |
| Deluge | `docs/zoho/creator-renewals-desk/deluge/` |
| Desk rules (tested) | `hermes/renewals/desk.py` |

## 0. Discover tools

List the Creator MCP tools. Write the exact names into
[`creator-mcp-inventory.md`](creator-mcp-inventory.md) (do not guess aliases).

Need at least: list applications, list forms, list reports, get report
metadata/data. Nice to have: environment status, publish, custom report
actions.

If the pack is **records-only**, inventory the live app and report gaps.
Do not fake page/workflow creates.

## 1. List applications

Find **Renewals Desk** (link name likely `renewals-desk`).

| Outcome | Next |
|---|---|
| App exists | Capture `workspace_name`, `link_name`, `created_by` |
| App missing | Create only if an MCP tool can create an application. Otherwise tell L to create it From scratch (name **Renewals Desk**) and stop |
| Multiple matches | Do not pick. List them and stop |

## 2. Inventory live schema

For that app, list forms, reports, pages/sections, and CRM integrations.

Expected CRM integrations (not Creator-native duplicates):

- Accounts, Deals, Policies, Renewal_Events, Renewals, AMS_Write_Queue, Tasks

Expected reports (from `reports.md`):

| Logical name | CRM module | Criteria |
|---|---|---|
| Worklist | Renewals | Dismissed false/empty |
| Needs verification | Renewal_Events | Eligibility = needs_verification |
| AMS pending | AMS_Write_Queue | object_type renewal, needs_approval or queued without Approved_By |
| AMS failed | AMS_Write_Queue | object_type renewal, status failed/dead |

Fill [`creator-mcp-inventory.md`](creator-mcp-inventory.md) with **live**
link names. Hermes sync jobs need those names later.

## 3. Gap check against the spec

Compare inventory to PR #353. For each gap, say whether MCP can fix it or
the IDE/Zia must.

Hard rules:

- Do not create Creator forms that duplicate Policies / Renewals as a second
  book.
- Do not call NowCerts from Deluge or MCP.
- AMS enqueue payload is structured JSON (`action`, `renewal_id`,
  `policy_number`, `expected_result`). Operators never type JSON.
- Stage/window/action rules must match `hermes/renewals/desk.py`.

## 4. Data / publish actions (only with a matching tool)

Safe, in order:

1. Read worklist report (empty is OK before `hermes --sync-zoho-renewals`).
2. If a custom action exists for AMS Approve, do **not** fire it in prod
   without Lamar. Dry-run / metadata only.
3. Environment publish only when Lamar asked to promote stage → production.

Forbidden without explicit approval: delete records, publish production,
approve AMS jobs, dismiss live renewals.

## 5. Hermes side (not MCP)

MCP does not replace these jobs (PR #353):

```
hermes --sync-zoho-renewals --sync-zoho-renewals-dry-run
hermes --sync-zoho-ams-queue
```

Cron after `--renewal-refresh` (2:30am ET): 2:35 upsert, 2:40 queue mirror.

Those jobs need CRM OAuth (`ZOHO_CLIENT_ID` / `SECRET` / `REFRESH_TOKEN`),
not Creator MCP.

## 6. Done when

- Inventory file has live workspace + app + report link names.
- Gap list is explicit (MCP vs IDE).
- No NowCerts writes were attempted from Creator.
