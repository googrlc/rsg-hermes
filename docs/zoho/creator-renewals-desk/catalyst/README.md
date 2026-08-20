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
cd renewals
npm start
```

If Hermes lives somewhere else, set `HERMES` to that path.

Browser: `http://localhost:3000`. You should see Commercial (blue) and
Personal (sage) pills, plus amber “Nearing deadline” and rose “Overdue”.

Do not run `npm run eject`. Do not `catalyst deploy` until Lamar asks.
