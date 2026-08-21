# Patch the live `renewals_desk_function`

Do **not** create a second Catalyst project. Merge this OS layer into
`~/catalyst-renewals-desk/functions/renewals_desk_function`.

Hermes is still the only NowCerts writer. Completing a checkpoint must
**not** call NowCerts and must **not** advance `Desk_Stage` / `Stage`.
Continue / `POST /next` still advances when the stage CRM task is Completed
(`taskIsDone`). Persist `Checkpoint_State` on the Zoho **Renewals** record.
Do not write Supabase `renewals_master` or `renewal_checklist_items`.

## Copy

From Hermes:

```bash
HERMES="$(git -C ~/rsg-hermes rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel)"
FUNC=~/catalyst-renewals-desk/functions/renewals_desk_function
cp "$HERMES/docs/zoho/creator-renewals-desk/catalyst/functions/renewals_desk_function/operating.js" "$FUNC/operating.js"
cp "$HERMES/docs/zoho/creator-renewals-desk/catalyst/functions/renewals_desk_function/os.js" "$FUNC/os.js"
```

## Merge into the existing Advanced I/O handler

At the top of `index.js`:

```js
const osDesk = require("./os");
```

1. After you build the GET `/api/desk` payload:

```js
payload = osDesk.attachOsToDeskPayload(payload);
```

That attaches the scorecard **and** hides desk-only leftovers (no `Deal_Id` /
`Related_Deal`).

2. After you build GET `/api/desk/renewals/:id` (the object with
   `renewal`, `tasks`, `next`), wrap it:

```js
payload = osDesk.attachOsToCard(payload);
```

3. Add POST `/api/desk/renewals/:id/checkpoints/:key/complete`:

```js
const result = osDesk.completeCheckpointOnCard(card, key, body);
if (!result.ok) return res.status(400).json(result);
// PUT Renewals Checkpoint_State = result.checkpoint_state
// If result.task_complete, mark the live STAGE CRM task Status=Completed
// (subject = result.stage_task_title or result.stage_task_aliases).
// Do not write Desk_Stage. Do not write NowCerts.
return res.json(result);
```

4. POST `/next` still advances one stored stage when `taskIsDone`. Keep that
   gate. Do not skip. Closed still requires Disposition.

Do not `catalyst deploy` to production. Development project only, and only
when Lamar asks.

Rules that must stay true:

- `Deal_Id` / `Related_Deal` 1:1 with Opportunity_Type=Renewals
- Worklist hides rows with no pipeline Deal
- Completing a checkpoint does **not** advance Desk_Stage
- `actor=hermes` never advances
- Continue stays disabled until the stage CRM task is Completed
- Close UI keeps Renewed / Rewritten / Lost — Price / Lost — Coverage /
  Lost — No response / Do not renew. Carrier download vs Enter in NowCerts
  (`Is_Download`) for renewed/rewritten.
