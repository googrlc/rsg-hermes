# Live Zoho Creator inventory (fill from MCP)

Do not invent names. Copy values returned by Creator MCP. Leave `TBD` if
the server is not attached.

**Captured:** TBD  
**Agent / environment:** TBD  
**MCP server name:** TBD  

## Tools discovered

| Tool name | Used for |
|---|---|
| TBD | |

## Application

| Field | Live value |
|---|---|
| Display name | TBD (expect **Renewals Desk**) |
| `link_name` | TBD |
| `workspace_name` | TBD |
| `created_by` | TBD |
| Environment (dev / stage / prod) | TBD |

## CRM integrations

| Module | Present? | Notes |
|---|---|---|
| Accounts | TBD | |
| Deals | TBD | |
| Policies | TBD | |
| Renewal_Events | TBD | |
| Renewals | TBD | |
| AMS_Write_Queue | TBD | |
| Tasks | TBD | |

## Forms / pages

| Spec | Live link name | Match? |
|---|---|---|
| Page `Desk` | TBD | |
| Page `Card` | TBD | |

## Reports

| Spec | Live link name | Module | Criteria match? |
|---|---|---|---|
| Worklist | TBD | Renewals | |
| Needs verification | TBD | Renewal_Events | |
| AMS pending | TBD | AMS_Write_Queue | |
| AMS failed | TBD | AMS_Write_Queue | |

## Gaps

| Gap | MCP can fix? | Blocked on |
|---|---|---|
| Creator MCP not on cloud environment (2026-08-18) | No | Add server to [environment](https://cursor.com/dashboard/cloud-agents/environments/e/2097123b-99aa-11f1-ba66-0e7d0216e441), relaunch |
