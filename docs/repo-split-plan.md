# Splitting rsg-hermes into per-app repos

Goal: one repo per app, so each app can be troubleshot, tested, and specialized
on its own. Ordered so that every step is independently shippable and reversible,
and so nothing depends on the whole migration finishing.

Baseline when this started (2026-08-01, `origin/main` @ 859d5a3):
36,402 lines of Python, 22 subpackages, 118 routes in one 4,601-line `api.py`,
1,600 passing tests, four containers built from one image.

## What was already split

Four of the six apps already have their own repos. They own the **UI/edge**;
rsg-hermes is the **shared backend all of them call**.

| App | Repo | Port |
|---|---|---|
| CRM (portal) | `rsg-agency-portal` | 8447 |
| Carriers | `rsg-carrierhub` | 3200 / 8445 |
| Finance | `rsg-commission-tracker` | 8446 |
| Intake gateways | `rsg-cptintake` (`rsg-intake-gate`) | 8790 |

The portal proxies to exactly three backends (`rsg-agency-portal/server.js`):
`rsg-hermes-api:8787`, `rsg-intake-gate:8787`, and carrierhub. So the split that
remains is of the **backend**, not of the front ends.

## Why layering comes before repo-cutting

The measured import graph had 90 cross-package edges and 13 bidirectional package
pairs. Cutting repos across a cycle means both repos vendor each other, so they
release in lockstep — worse to troubleshoot, not better.

But the cycles were not 13 separate tangles. They were **three misplaced things**,
each repeated across many domains:

1. **Queue contract inside the domains.** `scheduler/retry.py` imported six
   domain executors purely for their `OBJECT_TYPE_*` string constants, and those
   same executors imported `scheduler.retry.due_filter` back. Constants, not
   behavior — pure accidental coupling.
2. **`nowcerts_client.py` filed under `sync/`.** It is an API client that 23
   modules need. Its location made every consumer look like a dependent of the
   sync jobs.
3. **`dispatcher.py` and `nl_agent.py` filed under `core/`.** They are the
   top-layer NL router and import `commands`, `operations`, `sync`,
   `command_center`. Because `core/__init__.py` re-exported `Dispatcher`,
   importing *any* core utility pulled in `operations.write_gate`.

Fixing those three establishes the layering the repo split needs:

```text
        scheduler / agent / api        <- orchestration, top
   renewals cases intake finance ...   <- domains, siblings, no cross-imports
   hermes.core + hermes.integrations   <- primitives + clients, depends on nothing
```

Phase 1 does exactly that and nothing else — a pure refactor, no behavior
change, no route change, same 1,600 tests.

### Phase 1 result

| | before | after |
|---|---|---|
| Bidirectional package pairs | 13 | 1 |
| Modules pulled in by `import hermes.core.field_utils` | 6 | 3 |
| `hermes/core` imports outside itself | yes | **none** — it is a leaf |

`hermes.core` importing nothing outside itself is the property that makes
Phase 4 possible: the bottom layer can be lifted into its own package without
dragging a domain along.

The one remaining cycle is **`ams` ↔ `sync`**: `ams.book` imports
`sync.canonical_book_sync`, and `sync.commission_sync` / `sync.opportunity_sync`
import `ams.book`. Unlike the other twelve this is not a misfiling — the two
modules are both "the canonical book mirror" and the overlap is real. Left
as-is deliberately: both land in the hub repo, so it blocks no extraction, and
merging them is a design change that does not belong in a refactor. Worth doing
before anyone tries to split `ams` and `sync` from each other.

## Target topology

### `rsg-hermes-core` — the shared bottom layer (extracted as a package)

Every domain repo depends on this; it depends on no domain.

- Clients: `supabase_client`, `nowcerts_client`, `nextcloud_client`,
  `nextcloud_deck`, `ms365_client`, `gmail_client`, `slack_notifier`,
  `supermemory_client`, `retrieval_client`, `team_notify`
- Primitives: `identity`, `field_utils`, `due_dates`, `phi`, `schema_registry`,
  `surfaces`, `llm_client`
- Queue contract: `queue_types` — object types, destinations, `due_filter`
- Shared write surface: `overrides` (`portal_overrides`)

### Domain targets — four of six go to repos that already exist

**Do not create `rsg-hermes-finance` / `-carriers` / `-intake` / `-crm`.** Those
apps already have repos. A parallel `rsg-hermes-*` set would mean two repos per
app, which is the opposite of the goal. Only cases and renewals have no home.

Order is by measured coupling — cheapest and safest first. Route counts are the
routers built in Phase 2.

| # | Router | Target repo | Owns | Routes | Coupling |
|---|---|---|---|---|---|
| 1 | `routers/finance.py` | **`rsg-commission-tracker`** (exists) | `commissions/*`, `jobs/commission_*`, `sync/commission_sync` | 12 | Outbound only (`ams`, `core`, `overrides`); 1 inbound. Nearly free. |
| 2 | `routers/carriers.py` | **`rsg-carrierhub`** (exists) | `carriers.py` (68 lines) | 1 | carrierhub already serves its own `/api/carriers`. Mostly a deletion — see the collision note below. |
| 3 | `routers/cases.py` | **new** — no existing repo | `casework/*` + the generic half of `renewals/cases.py` | 21 | Blocked on the `renewals/cases.py` split below. |
| 4 | `routers/intake.py` | **`rsg-cptintake`** (exists) | `intake/*`, `command_center/{extract,ocr,quote_extract,synthesis,intake_executor,submission,validators,review,router}`, `operations/intake_worker` | 13 | `sync.opportunity_*` imports `intake.opportunities` 6× — CRM pipeline code misfiled under intake; move it to the hub first. |
| 5 | `routers/renewals.py` | **new** — no existing repo | `renewals/*`, `operations/renewal_tracker` | 6 | Most central. Extract last. |
| — | `api.py` (stays) | `rsg-hermes` + **`rsg-agency-portal`** for the UI | the hub: app shell, clients/opportunities/quotes/policies/documents/deck, `ams`, `sync`, `book_sync`, `scheduler`, `agent`, `commands`, `proposals` | 65 | The remainder, not an extraction. |

Related repos that are not split targets: `rsg-infrastructure` (deploy/infra) and
`rsg-obsidian-vault` (notes).

#### Two naming collisions to fix before consolidating

- **`/api/intake` vs `/api/intakes`.** `rsg-cptintake` is the NowCerts
  *submission gateway* (`rsg-intake-gate`, an MCP/AMS relay with its own operator
  UI) and owns `/api/intakes/*` and `/api/intake/documents`. `routers/intake.py`
  is the *CRM intake desk* — leads, the intake queue, agency-intake drafting.
  The portal routes between the two backends on that single trailing "s"
  (`rsg-agency-portal/server.js`). Fix the naming before merging them.
- **Two `/api/carriers`.** Hermes serves one off Supabase; carrierhub serves
  another on :3200/:8445 with a different shape. They are not interchangeable.

#### The polyglot question (decide before extraction #1)

`rsg-commission-tracker`, `rsg-carrierhub` and `rsg-agency-portal` are Node/TS
front ends. The routers are Python/FastAPI. Moving a router into one of those
repos makes it polyglot — two toolchains, two Dockerfiles, one repo. Only
`rsg-cptintake` is already Python. Three ways to go:

1. **Polyglot app repos.** Each app repo holds its UI *and* its Python backend.
   Truly one repo per app; costs two toolchains per repo.
2. **Backends stay in `rsg-hermes`.** App repos remain front-end only. Cheapest,
   but "one repo per app" stays half-true — which is roughly today.
3. **Split by runtime.** Extract Python backends only where the target is
   already Python (intake → cptintake), leave the rest mounted in the hub.

This is a real fork and it is not mine to pick. Nothing in Phase 2 depends on
it — the routers are the same either way.

## Phases

- **Phase 1 — layering (this PR).** Break the three misplacements above. No new
  repos, no behavior change. Unblocks everything else and immediately shrinks the
  blast radius of a shared-client change.
- **Phase 2 — routers.** Split `api.py`'s 118 routes into per-domain
  `APIRouter`s, one file per app, mounted by a thin shell. Makes the future repo
  boundary visible in one file and reviewable before any code moves out.
- **Phase 3 — processes.** Give each domain its own container/port off the same
  image. This is what actually delivers failure isolation: today all 118 routes
  share one uvicorn worker with 85 of 93 handlers declared `async def` over sync
  bodies, so one slow renewal call stalls finance, cases, and intake with it
  (measured 0.17s → 28.4s).
- **Phase 4 — `rsg-hermes-core`.** Extract the bottom layer as an installable
  package, pinned by commit in each consumer.
- **Phase 5 — repos.** Extract domains one at a time in the order above, with
  `git subtree split` to preserve history. Each gets its own `CLAUDE.md`, skills,
  and test suite.

## Open questions to settle before Phase 5

- **`/api/tasks` ownership.** It touches both `casework` and `renewals`
  (renewal tasks vs case tasks). Either cases owns tasks and renewals calls it,
  or tasks become their own service. Needs a decision before cases is cut.
- **The shared Supabase schema.** All domains read and write the same tables
  (`canonical_policies`, `agency_crm_*`, `outbound_sync_queue`,
  `renewal_candidates`). Separate repos do **not** separate the database.
  Migrations stay centralized in `supabase/migrations` unless and until table
  ownership is assigned per domain — that is a bigger decision than this split.
- **`intake.opportunities`** (482 lines) is the CRM pipeline model, not intake.
  It moves to the hub in Phase 2 or intake cannot cleanly leave.
