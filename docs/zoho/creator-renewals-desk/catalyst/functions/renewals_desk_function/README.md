# Patch the live `renewals_desk_function`

Do **not** create a second Catalyst project. Merge this OS layer into
`~/catalyst-renewals-desk/functions/renewals_desk_function`.

Hermes is still the only NowCerts writer. Completing a checkpoint must
**not** call NowCerts. AMS still goes through `AMS_Write_Queue`.

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

1. After you build the GET `/api/desk` payload, map rows:

```js
payload.rows = (payload.rows || []).map(osDesk.attachOsToDeskRow);
```

2. After you build GET `/api/desk/renewals/:id` (the object with
   `renewal`, `tasks`, `next`), wrap it:

```js
payload = osDesk.attachOsToCard(payload);
```

3. Add POST `/api/desk/renewals/:id/checkpoints/:key/complete`:

```js
const result = osDesk.completeCheckpointOnCard(card, key, body);
if (!result.ok) return res.status(400).json(result);
// Mark the matching CRM task Status=Completed (subject = checkpoint title
// or alias). Do not write NowCerts.
if (result.advanced) {
  // PUT Renewals Stage / Desk_Stage = result.desk_stage (one step only).
  // Closed still requires Disposition. Never skip.
}
return res.json(result);
```

4. POST `/next` must refuse if `result.remaining.length > 0` after attaching OS.

Do not `catalyst deploy` to production. Development project only, and only
when Lamar asks.

Rules that must stay true:

- Related_Deal 1:1 with Opportunity_Type=Renewals
- `actor=hermes` never advances Desk_Stage
- No skipped stage
- Completing a checkpoint while required items remain does **not** advance
