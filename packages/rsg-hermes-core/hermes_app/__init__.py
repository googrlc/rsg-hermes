"""The web layer shared by every RSG Hermes app.

`hermes_core` and `hermes_integrations` know nothing about HTTP. This package is
the part every app repo needs to *be* a service: the request plumbing (client
singletons, the bearer gate, the agency_crm_users guard) and the app factory
that turns a set of routers into a running FastAPI process.

It ships in the same distribution as the core because the alternative is a copy
of `deps.py` in each of six repos, which is precisely the lockstep-release
problem the split exists to avoid — a fix to the Supabase singleton would become
six coordinated PRs.

It is separate from `hermes_core` because that package deliberately has no web
framework in it. Importing a queue constant should not pull in FastAPI.
"""
