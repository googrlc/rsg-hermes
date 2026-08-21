# Catalyst Renewals Desk (live product)

The live workstation is the **Zoho Catalyst SPA** in project `935150771`,
function `renewals_desk_function`, CRM-embedded widget at
`https://renewals-desk-935150771.development.catalystserverless.com`.

The Zoho Creator app `renewals-desk` in workspace `lamar_risksolutionsgroup668`
is an **empty stub** (native forms, 0 records). Do **not** fill Creator. Do
**not** rewrite the OS in Creator. Evolve this Catalyst client + function.

Hermes remains the only NowCerts writer. Zoho CRM module `Renewals` is the
primary object. Do not use empty Supabase `renewals_master` /
`renewal_checklist_items` as a source of truth.

These files were recovered from the live source map (`main.9fe345c9.js.map`)
and then overlaid with the Renewal Health scorecard + in-place checkpoints.
Keep `WORK_STEPS` / `taskIsDone` from `workflow.js`. Continue stays disabled
until the stage CRM task is Completed. Completing checkpoints on the card
marks that task so the user never hunts CRM.

## Copy onto the Mac Mini (`~/catalyst-renewals-desk/renewals/src`)

```bash
HERMES="$(git -C ~/rsg-hermes rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel)"
SRC="$HERMES/docs/zoho/creator-renewals-desk/catalyst"
DEST=~/catalyst-renewals-desk/renewals/src
git -C "$HERMES" pull --ff-only origin cursor/fill-creator-renewals-desk-4189 || true
cp "$SRC/App.js" "$SRC/api.js" "$SRC/workflow.js" "$SRC/crmLaunch.js" "$SRC/operating.js" "$SRC/App.css" "$DEST/"
mkdir -p "$DEST/components"
cp "$SRC/components/"*.js "$DEST/components/"
cd ~/catalyst-renewals-desk/renewals && npm start
```

Then merge the function OS patch:
[`functions/renewals_desk_function/README.md`](functions/renewals_desk_function/README.md).

Do not `catalyst deploy` until Lamar asks. No production publish.
