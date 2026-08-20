# Nextcloud Team Folders (agency document store)

This is the shared file store for RSG client documents. **Team Folders**
(Nextcloud app id `groupfolders`, formerly Group Folders) are admin-owned
mounts. They are visible to every member of a group automatically, have their
own quota, cannot be unshared by a staff user, and keep a stable WebDAV path
for Zoho / Hermes uploads.

Do **not** share a folder out of one person's Files tree. That folder lives in
that user's quota, disappears if they leave, and does not have a consistent
path for every login.

## Live instance (applied 2026-08-20)

| Item | Value |
|---|---|
| Host | `https://nextcloud-x6wle-u69864.vm.elestio.app` (Nextcloud 34.0.2) |
| Staff group | `All Team` — Gretchen Coates, Lamar, hermes, root |
| ACL subgroups | `Commercial Lines` (Lamar, hermes), `Personal Lines` (Gretchen Coates, hermes), `Management` (Lamar, root) |
| Service account | `hermes` (in `All Team`; do not create `agency_bot`) |
| Admin | Elestio user `root` |
| Team Folders app | **enabled** (`groupfolders`) |
| Team Folder | **Agency Documents** (id 1), All Team permissions 31, quota 500 GiB |
| Lanes | `Agency Documents/Commercial Lines`, `…/Personal Lines`, `…/Claims` |

Hermes already sees `Agency Documents` at WebDAV root. Personal folders named
`Commercial Lines/` / `Personal Lines/` / `Clients/` still exist beside the
mount — leave them; file **new** work under the Team Folder. Do not MKCOL a
second personal folder named `Agency Documents`.

## Canonical tree

```
Agency Documents/                 ← Team Folder (everyone in All Team sees this)
├── Commercial Lines/
│   └── {Account}/
│       └── {Policy type}/        e.g. General Liability, Property, Workers Comp
│           └── {Document type}/  e.g. Declaration Pages, Loss Runs, Quotes
│               └── {Year}/
├── Personal Lines/
│   └── {Household}/
│       └── {Policy type}/
│           └── {Document type}/
│               └── {Year}/
└── Claims/
```

Hermes path helper: `NextcloudClient.agency_account_dir` /
`ensure_agency_account_tree`. After the mount exists, set
`NEXTCLOUD_BASE_PATH=Agency Documents` so those helpers prefix correctly.

Legacy intake/renewal filing still uses `Clients/{client}/{category}/` until
those accounts are cut over. Do not bulk-move `Clients/` without a separate
migration.

## CRM / WebDAV paths

Authenticate as `hermes`. Team Folder files appear at:

```
/remote.php/dav/files/hermes/Agency Documents/Commercial Lines/...
```

Store **Files UI** links in Zoho (users are already Nextcloud users):

```
https://nextcloud-x6wle-u69864.vm.elestio.app/apps/files/?dir=/Agency Documents/Commercial Lines/{Account}/...
```

`NextcloudClient.files_ui_url(rel)` builds that. WebDAV URLs work but prompt
for login; public share links are optional per file (`shareType=3`) when a
document must open without a Nextcloud session.

## One-time admin apply

The Hermes filing user is a **subadmin of `All Team`**, not a full admin, so
it cannot enable apps or create Team Folders.

1. In Nextcloud, log in as **`root`** (Elestio admin).
2. **Apps → Team folders** (or Group folders) → **Enable**.
3. Personal settings → Security → create an app password for `root`.
4. From a machine with Hermes Nextcloud env vars:

```bash
source .venv/bin/activate
NEXTCLOUD_ADMIN_USER=root \
NEXTCLOUD_ADMIN_APP_PASSWORD='app-password' \
  python scripts/nextcloud_team_folders_setup.py --apply
```

Or, still as `root` in the web UI:

1. Administration Settings → Team folders → create **Agency Documents**.
2. Add group **All Team** with Write, Share, and Delete.
3. Set a quota (500 GB recommended).
4. Create `Commercial Lines`, `Personal Lines`, and `Claims` inside it.

Then set `NEXTCLOUD_BASE_PATH=Agency Documents` on the Hermes API container
(Elestio env / 1Password). Do not commit the value as a secret; it is not one.

Status / dry-run (works as `hermes`, no writes):

```bash
python scripts/nextcloud_team_folders_setup.py
```

Optional ACL subgroups (`Commercial Lines`, `Personal Lines`, `Management`)
are created by `--apply` unless you pass `--skip-optional-groups`. Enabling
advanced permissions on subfolders still needs the Nextcloud admin UI or
`occ groupfolders:permissions` on the Nextcloud box (this Cloud agent cannot
SSH to that VM).

## What this repo will not do

- Create `agency_bot` — `hermes` is already the service account.
- Create a personal `Agency Documents` folder via WebDAV.
- Move existing `Clients/` files into the Team Folder.
- Store Nextcloud passwords in git.
