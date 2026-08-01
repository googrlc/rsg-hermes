"""Per-app HTTP routers.

One module per app, each owning its own routes, request models and helpers, so
the boundary a future repo split will cut along is visible in one place before
any code leaves this repo. `hermes/api.py` is the shell that builds the app and
mounts these.

Shared plumbing (the Supabase/NowCerts singletons, the bearer gate, the
agency_crm_users guard) lives in `deps` — routers depend on it, never on each
other or on `hermes.api`.

See docs/repo-split-plan.md, Phase 2.
"""
