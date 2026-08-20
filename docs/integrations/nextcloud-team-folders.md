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
│           └── {Document type}s/ e.g. Declaration Pages, Loss Runs, Quotes
│               └── {Renewal cycle}/
│                   {Carrier} {file}
├── Personal Lines/
│   └── {Household}/
│       └── {Policy type}/
│           └── {Document type}s/
│               └── {Renewal cycle}/
└── Claims/
```

Example: `Agency Documents/Commercial Lines/ABC Roofing/General Liability/Declaration Pages/2027/Travelers GL Dec Page.pdf`

Path helpers: `hermes_integrations.nextcloud_paths.canonical_rel_path` (registry pipeline) and `NextcloudClient.agency_account_dir`. The registry helper prefixes `Agency Documents/` when `NEXTCLOUD_BASE_PATH` is unset, so new registry uploads land in the Team Folder without waiting on the env cutover. After the mount exists, set `NEXTCLOUD_BASE_PATH=Agency Documents` so the client also prefixes correctly for other helpers.

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

## Document Registry (Zoho metadata + Nextcloud files)

Zoho CRM is the metadata registry. Nextcloud is the file store. **Hermes** is
the integration layer — n8n was never deployed, and Deluge is not required.

| Layer | Role |
|---|---|
| Zoho custom module `Document_Registry` | Searchable metadata (account, type, carrier, dates) |
| Nextcloud Team Folder | The actual file (WebDAV PUT, `OC-FileId`) |
| Hermes `POST /api/document-registry/upload` | Derive path → PUT → write CRM only if URL exists |

**Golden rule:** no `Document_Registry` row without `Nextcloud_File_URL`. Hermes
uploads first, then upserts Zoho (when `HERMES_WRITE_TO_ZOHO=1`). A failed PUT
never creates a CRM record.

Stand up the Zoho module (dry-run default; needs settings OAuth scopes to apply):

```bash
python scripts/zoho_document_registry_setup.py
```

Field pack: `docs/zoho/fields_document_registry.csv`. Search:

```
GET /api/document-registry/search?account_name=ABC%20Roofing&document_type=Declaration%20Page&carrier=Travelers
```

MCP: `document_registry_upload` / `document_registry_search`. Legacy
`file_to_nextcloud` still files into the older client-tree endpoint.

## Remaining: point Hermes at the mount

The mount is live. Hermes still files into the personal `Clients/` tree until
`NEXTCLOUD_BASE_PATH=Agency Documents` is set on the Hermes API container
(Elestio env / 1Password). That is not a secret. Do not restart production
without an explicit go-ahead.

Status / dry-run:

```bash
python scripts/nextcloud_team_folders_setup.py
```

`--apply` is idempotent. Optional ACL subgroups already exist. Enabling
advanced permissions on subfolders still needs the Nextcloud admin UI or
`occ groupfolders:permissions` on the Nextcloud box.

## What this repo will not do

- Create `agency_bot` — `hermes` is already the service account.
- Create a personal `Agency Documents` folder via WebDAV.
- Move existing `Clients/` files into the Team Folder.
- Store Nextcloud passwords in git.
