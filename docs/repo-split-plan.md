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

#### Polyglot app repos — DECIDED (2026-08-01)

Each app repo holds its UI **and** its Python backend. `rsg-commission-tracker`,
`rsg-carrierhub` and `rsg-agency-portal` are Node/TS today, so they become
two-toolchain repos: two Dockerfiles, two dependency manifests, one app.
`rsg-cptintake` is already Python.

What that costs, so nobody is surprised by it later: every app repo needs a
Python toolchain in CI, and a change to the shared layer means bumping a pin in
up to six places. What it buys is the thing that was asked for — one repo per
app, each with its own tests, its own CLAUDE.md, and no reason to open another.

The pin is per-repo and by sha, so a core change reaches each app only when that
app chooses to take it. That is deliberate: six repos moving in lockstep on
every core commit would be the monolith again, with more steps.

## Phases

- **Phase 1 — layering (this PR).** Break the three misplacements above. No new
  repos, no behavior change. Unblocks everything else and immediately shrinks the
  blast radius of a shared-client change.
- **Phase 2 — routers.** Split `api.py`'s 118 routes into per-domain
  `APIRouter`s, one file per app, mounted by a thin shell. Makes the future repo
  boundary visible in one file and reviewable before any code moves out.
- **Phase 3 — processes. DONE.** Two separate problems, and the first was the
  one actually causing the outages:

  1. **An app freezing itself.** 111 of the 118 handlers were `async def` with
     no `await` in the body. FastAPI runs those ON the event loop, so every
     blocking Supabase/NowCerts call stopped the whole process — measured
     turning a 0.17s endpoint into 28.4s, presenting as "the CRM buttons don't
     work". Declaring them `def` hands them to a 40-worker threadpool.
     `tests/test_event_loop_not_blocked.py` prevents a relapse; the mistake is
     invisible in review because `async` reads as more modern, not as a
     process-wide stall. Two consequences handled with it: the lazy client
     singletons in `routers/deps.py` are now reached from many threads, so they
     got double-checked locking (losing that race against NowCerts costs a
     second ~26s password grant), and `SupabaseClient.pool_maxsize` went 20 → 40
     to match the threadpool.

  2. **One app freezing the others.** `hermes/services.py` holds a registry of
     six services — name, routers, path prefixes, port, and the queue object
     types its worker drains. `create_app("finance")` returns an app carrying
     only the finance routes. Each gets its own process, event loop, threadpool,
     restart and log stream, via `docker-compose.services.yml`.

  Both are **opt-in**: `create_app("all")` returns the existing
  `hermes.api.app` object unchanged and is the default, so a deploy that sets no
  `HERMES_SERVICE` behaves exactly as before.

  ```bash
  docker compose -f docker-compose.yml -f docker-compose.services.yml \
    --profile services up -d --build          # add --profile workers for the queues
  ```

  **The one rule:** never run `hermes-api` and the split services against the
  same traffic, and never run `hermes-scheduler` alongside the per-service
  workers — the unsplit scheduler claims every object type and would race them
  for the same queue rows.

  What `tests/test_services.py` guarantees, because these fail silently
  otherwise: the union of the split services serves *exactly* the routes the
  single app serves (an unclaimed route works in the monolith and 404s once
  split), no two services claim the same route or prefix, every declared prefix
  has routes behind it, every queue object type has exactly one drainer, and the
  compose ports match the registry.

  **Services talking to each other.** They share one image and one database, so
  a service needing another app's *logic* imports it — no HTTP hop. `base_url()`
  in the registry is for reaching another app's HTTP surface, which is what
  separate images or repos will need. The ports are already there, so that day
  is a config change rather than a redesign.
- **Phase 4 — `rsg-hermes-core`. DONE (in-repo).** The bottom layer is now a
  real distribution at `packages/rsg-hermes-core`, providing two top-level
  packages so the import rewrite was a pure prefix swap and no name is shared
  between distributions:

  | was | is |
  |---|---|
  | `hermes.core.*` | `hermes_core.*` |
  | `hermes.integrations.*` | `hermes_integrations.*` |

  Namespace-packaging `hermes` across two distributions was the alternative and
  would have avoided the rewrite, but two independently-versioned repos writing
  into one top-level name is a known way to get import failures that depend on
  install order. With six repos installing this, distinct roots are worth the
  churn.

  Three things moved to make it genuinely standalone:

  - **`intake_submissions.py` left the core** for `hermes/intake/submissions.py`.
    It sat in `integrations/` on the strength of the word "integration" while
    reading and writing the `intake_submissions` table and running that
    pipeline's state machine. Shipping it in the shared core would have put
    intake's schema inside the finance and carriers repos.
  - **`personas/` and `data/`** moved *inside* `hermes_core`, next to the
    `identity.py` and `schema_registry.py` that load them — at the distribution
    root they would not have shipped in the wheel.
  - The Dockerfile installs the core **before** the app, since the app imports
    it at module scope.

  `tests/test_core_is_a_leaf.py` enforces both halves of the rule: no core
  module may import `hermes.*` (an app import here drags that app into all six
  repos), and no core module may read or write an app-owned table (domain logic
  wearing a client's name). Neither failure looks wrong in the diff that
  introduces it — which is how the first thirteen cycles happened.

  **Published.** `github.com/googrlc/rsg-hermes-core` (private) is live and
  verified: a fresh clone imports all three packages and builds a working
  FastAPI service from `hermes_app.service`.

  **This repo stays the source of truth**; the standalone repo is a mirror,
  published by `scripts/publish-core.sh`. Never commit to it directly. Editing
  the core in its own repo and vendoring it back would give two places to change
  one file, which is the failure mode the whole split exists to remove. App
  repos pin the mirror by sha, so publishing moves nobody until they choose to
  move.

  A third package joined the distribution once the app closures were measured.
  `cases` and `renewals` each need three things that are neither core primitives
  nor domain code — the request plumbing, the `portal_overrides` store, and the
  service factory:

  | was | is |
  |---|---|
  | `hermes/overrides/*` | `hermes_core.overrides` |
  | `hermes/routers/deps.py` | `hermes_app.deps` |
  | generic half of `hermes/services.py` | `hermes_app.service` |

  `hermes_app` is separate from `hermes_core` so that reading a queue constant
  does not pull in FastAPI. `hermes/services.py` keeps only the registry of
  *which* services exist — the part an app repo declares for itself.

  The leaf guard paid for itself here: `deps.get_dispatcher` built the
  natural-language Dispatcher, importing `hermes.agent`. A shared layer that
  constructs the hub's router would have put the hub inside every other app
  repo. Nothing in the diff looked wrong; the test is what noticed.
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
