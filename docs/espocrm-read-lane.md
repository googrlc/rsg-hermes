# EspoCRM Read Lane (direct Postgres) + Write Lane (through Espo)

Hermes is becoming EspoCRM's intelligent backend brain. The design rule that makes
that safe:

> **Read directly to _decide_. Write through Espo to _act_.**

Two deliberately asymmetric lanes.

## Why asymmetric

EspoCRM's coherence does **not** live in the database — it lives in the PHP
application layer: `beforeSave`/`afterSave` hooks (cascades, linking, derived
fields), Formula/Workflow/BPM (business rules), ACL, validation, and the Stream
(activity feed, followers, audit trail). The ORM also maintains schema invariants
the whole app trusts blindly: the `deleted` soft-delete flag, many-to-many link
tables, JSON-encoded fields, and `*_id`/`*_name` denormalized pairs.

- **Reads** trigger none of that, so reading raw tables is safe and fast — there is
  nothing to bypass.
- **Writes** that go straight to SQL bypass *all* of it: cascades silently don't
  fire, invariants get corrupted, ACL/validation are skipped, and the change is
  invisible to the audit trail. So writes stay in the application layer.

## Lane 1 — Read (implemented)

`hermes/integrations/espo_db.py` — a read-only Postgres connection.

**Two independent write guards** (either alone suffices; both together mean a Hermes
bug cannot mutate the CRM through this lane):
1. A dedicated DB role granted **`SELECT` only** (no INSERT/UPDATE/DELETE grants).
2. The session is forced read-only (`default_transaction_read_only = on`), and
   `_rows()` refuses anything but `SELECT`/`WITH`.

**Connection.** Reach the DB over an SSH tunnel rather than exposing Postgres on the
Tailnet:

```bash
ssh -N -L 6543:127.0.0.1:5432 hermes@espocrm-ts
```

Then set the (optional) env vars — absence keeps everything on the REST API:

```
ESPO_DB_HOST=127.0.0.1
ESPO_DB_PORT=6543
ESPO_DB_NAME=espocrm
ESPO_DB_USER=hermes_ro
ESPO_DB_PASSWORD=…
```

**Setup on the DB (one-time, by a DBA):**
```sql
CREATE ROLE hermes_ro LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE espocrm TO hermes_ro;
GRANT USAGE ON SCHEMA public TO hermes_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hermes_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO hermes_ro;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- fuzzy name matching
```

**Install + validate** (the doctor is read-only; safe to run anytime):
```bash
pip install -e '.[db]'
hermes --espo-db-doctor
```

**What it does** — the heavy relational work, done in-database:
- Duplicate detection / fuzzy matching via `pg_trgm` `similarity()` on names, plus
  exact email/phone matches joined through `entity_email_address` /
  `entity_phone_number`.
- Fast relational lookups for "where does this record fit" without N API round-trips.

**Schema pinning.** `REQUIRED_SCHEMA` lists the tables/columns we depend on;
`verify_schema()` (run by the doctor) reports drift so an Espo upgrade surfaces as a
clear error rather than silent corruption. Treat this lane as a fast read-cache, never
as source of truth.

## Lane 2 — Write (roadmap, not in this change)

Writes preserve all of Espo's logic. Three stages, adopt as far as needed:

- **Stage A (today): REST** via `EspoClient`. Correct; leave untouched.
- **Stage B: a custom EspoCRM extension** in the `rsg-espocrm` (PHP) repo — a custom
  API action that accepts a high-level *intent* and executes it with Espo's own ORM /
  record services, so every hook/ACL/Stream fires and compound operations
  (Lead + Contact link + cascade Tasks) run transactionally in one in-app call. This
  is "Hermes as a plugin." Hermes would call it via a thin `EspoExtensionClient`.
- **Stage C: `bin/command` CLI** over SSH for batch/maintenance jobs that should run
  in app context.

## The combined flow (e.g. ingesting an Outlook email)

1. **Read lane:** fuzzy-match the sender against Contacts/Accounts in-DB, detect
   duplicates, resolve where the Lead fits — fast, local. → _decide_
2. **Write lane:** send the resolved intent to Espo (REST now, extension later), which
   applies it coherently with hooks firing. → _act_
