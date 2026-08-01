# rsg-hermes-core

The shared bottom layer for the RSG Hermes apps. Every app repo — finance,
cases, intake, renewals, carriers, the hub — installs this and nothing installs
the apps.

Two top-level packages:

- **`hermes_core`** — primitives that carry no I/O of their own: the
  `outbound_sync_queue` contract (`queue`), the command-dispatch contract
  (`dispatch`), identity/persona loading, PHI redaction, field and due-date
  normalisation, the schema registry, the LLM client.
- **`hermes_integrations`** — one module per external system: Supabase,
  NowCerts, Nextcloud (+ Deck), Microsoft 365, Gmail, Slack, Supermemory, the
  retrieval index, and the team-notify fan-out.

## The rule

**Nothing in here may import an app.** That is the whole reason it can be
extracted: it depends on no domain, so a domain can depend on it without
dragging its siblings along. `tests/test_core_is_a_leaf.py` in the app repo
enforces it.

The corollary is that domain logic must not drift in. `intake_submissions.py`
lived in `integrations/` for a while — it reads and writes the
`intake_submissions` table and runs that pipeline's state machine, which is
intake's business, not a client. It now lives with the intake app. A module
belongs here only if it would make sense to an app that has never heard of
renewals or commissions.

## Versioning

Consumers pin a commit. A change here reaches six repos, so treat the public
surface as an API: additive changes are cheap, renames are not.

## Install

```bash
pip install -e packages/rsg-hermes-core          # from an rsg-hermes checkout
pip install 'rsg-hermes-core @ git+https://github.com/googrlc/rsg-hermes-core@<sha>'
```
