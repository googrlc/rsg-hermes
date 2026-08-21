# Live inventory — Creator Renewals Desk (empty stub)

Captured via Zoho Creator MCP against workspace `lamar_risksolutionsgroup668`.
**This Creator app is not the live product.** Live desk = Catalyst project
`935150771` / `renewals_desk_function`. Do not fill Creator. Do not invent
link names.

## Application

| Field | Live value |
|---|---|
| Display name | Renewals Desk |
| Production link name | `renewals-desk` |
| Development link name | `renewals-desk-development` |
| Workspace | `lamar_risksolutionsgroup668` |
| Workspace id | `935748147` |
| Application id (dev) | `4958918000000018002` |
| Production application id | `4958918000000018026` |
| Created | 18-Aug-2026 |
| Current environment | development |
| Development status | Created |
| Stage | Not Yet Published |
| Production | Not Yet Published |

One app only. No duplicate listed by `getApplications`.

## CRM modules (Zoho CRM, same org)

Workflow API accepts these module API names (INVALID_MODULE would mean missing):

| Module | Present |
|---|---|
| Accounts | yes (Hermes Account Client Queue A) |
| Deals | yes (Big Deal Rule) |
| Tasks | yes |
| Policies | yes (Hermes Policy Queue B; module id `7529682000000692001`) |
| Renewal_Events | yes |
| Renewals | yes (workflow config returned) |
| AMS_Write_Queue | yes |

## Creator components (development)

| Piece | Live |
|---|---|
| Pages Desk / Card | **live** `Desk`, `Card` (`getPages` 3000) |
| Native form | `Renewals_Desk` (type 1, category 1) — do **not** treat as a second book |
| Native report | `All_Renewals_Desks` |
| CRM integration forms | **live** `Accounts`, `Deals` (section category 5). Still missing: Policies, Renewal_Events, Renewals, AMS_Write_Queue, Tasks |
| CRM integration reports | **live** `Accounts_Report`, `Deals_Report` |
| Spec reports Worklist, Needs verification, AMS pending/failed, Open tasks | missing (need custom CRM modules in the datasource picker) |
| Sections | `Renewals_Desks` (native), `Desk` (pages), `Accounts`, `Deals` |
| Deluge (stage, window, tasks, enqueue, approve, dismiss) | not on CRM Renewals yet; `cursor_api` exists as Custom API |
| CRM connection | System connection `Zoho CRM` enabled; Renewals Desk access On |

Re-checked 18-Aug-2026 via Creator MCP (`environment: development`) after the logged-in IDE session: still **one** app (`renewals-desk`). Pages Desk/Card exist. Accounts and Deals are CRM integration forms. Custom CRM modules did not appear in **+ → Form → Using an Integrated Datasource → Zoho CRM → Module** (typing Policies / Renewal_Events / Tasks returned "No matches found"). Do not add more native book forms.

## IDE fill notes

Creator metadata MCP inventories the live app but cannot create pages or
CRM integrations. Fill is a logged-in Creator IDE session (LC).

Real click path (Creator 5):

1. **+** immediately to the right of the app name (not Cliq Smart Chat).
2. **Form** → **Using an Integrated Datasource** → **Zoho CRM** → pick one
   module → Create. Repeat per module.
3. **Page** → **Blank** → name `Desk` / `Card`.
4. Page builder: **Report** widget onto the canvas (HTML snippets in this
   builder validate as Deluge and reject raw HTML).

**Cliq "Here is your Smart Chat (Ctrl+Space)" is not Zia.** It searches
Chats / Contacts / Channels. Creator Zia for forms is **+ → Form → Using
Zia**. Do not paste the spec pack into Cliq search.

**Custom CRM modules** (`Policies`, `Renewal_Events`, `Renewals`,
`AMS_Write_Queue`) and even **Tasks** did not appear in the System Zoho CRM
datasource module dropdown. The System connection is Enabled with Renewals
Desk access On; it is not user-editable for extra modules from that
screen. Worklist / AMS reports stay blocked until those modules are in the
picker (or a custom connection with CRM custom-module scopes exists).

Never publish production from this fill.

## Custom API Cursor (live)

| Field | Value |
|---|---|
| Endpoint | `https://www.zohoapis.com/creator/custom/lamar_risksolutionsgroup668/Cursor` |
| Method | POST |
| Auth | OAuth2, admin |
| Header | `environment: development` (required until production is published) |
| Function | `desk.cursor_api` |
| Ping body | `{"action":"ping"}` |

## How to fill

Open **this** app in the Creator IDE. Use **+ → Form → Zoho CRM** for
integrations (not Cliq Smart Chat). Paste
[`ZIA_PASTE_PROMPT.md`](ZIA_PASTE_PROMPT.md) only into **+ → Form → Using
Zia** if you need AI help — never into Ctrl+Space Cliq search. Do not
create a new application.

## After fill (live link names from MCP)

| Logical name | Live link name |
|---|---|
| Desk page | `Desk` |
| Card page | `Card` |
| Accounts integration form | `Accounts` |
| Deals integration form | `Deals` |
| Accounts Report | `Accounts_Report` |
| Deals Report | `Deals_Report` |
| Worklist report | |
| Needs verification report | |
| AMS pending report | |
| AMS failed report | |
| Open tasks report | |
