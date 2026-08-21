# Update the Catalyst Renewals Desk app

The live React app is on the Mac Mini at
`~/catalyst-renewals-desk/renewals`. This folder is the drop-in UI.

Hermes remains the only NowCerts writer.

## One-time copy (Cursor terminal on the Mac)

From any directory, if Hermes is already cloned:

```bash
HERMES="$(git -C ~/rsg-hermes rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel)"
cd ~/catalyst-renewals-desk
git -C "$HERMES" pull --ff-only origin cursor/fill-creator-renewals-desk-4189 || true
cp "$HERMES/docs/zoho/creator-renewals-desk/pages/desk.css" renewals/src/desk.css
cp "$HERMES/docs/zoho/creator-renewals-desk/catalyst/App.js" renewals/src/App.js
cp "$HERMES/docs/zoho/creator-renewals-desk/catalyst/operating.js" renewals/src/operating.js
cd renewals
npm start
```

Then merge the function OS patch:
[`functions/renewals_desk_function/README.md`](functions/renewals_desk_function/README.md).

If Hermes lives somewhere else, set `HERMES` to that path.

Browser: `http://localhost:3000`. You should see a worklist of **renewals**
(not tasks), a health % / scorecard, and in-place checkpoints on the card.
Commercial is blue. Personal is sage. Completing a checkpoint does not skip
remaining required items. Hermes is the only NowCerts writer.

Do not run `npm run eject`. Do not `catalyst deploy` until Lamar asks.
