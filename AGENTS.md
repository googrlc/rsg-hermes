# Hermes Repo Instructions

This repository supports the Hermes AI operations environment for Risk Solutions Group.

## Rules

- Never push directly to `main`.
- Always create a branch using `hermes/<short-description>`.
- Always show `git diff` before committing.
- Never edit `.env`, credentials, tokens, secrets, private keys, or production connection strings without explicit approval.
- Never run destructive commands such as `rm -rf`, forced resets, database drops, or production restarts without explicit approval.
- Prefer small, reviewable commits.
- Explain every change in plain language.
- Before pushing, summarize:
  - files changed
  - purpose of change
  - risk level
  - rollback steps

## Cursor Cloud specific instructions

Hermes is a single Python 3.11+ app (`rsg-hermes`) with a shared path package
`rsg-hermes-core` under `packages/`. Two entrypoints: the `hermes` CLI and the
`hermes-api` FastAPI backend (which also serves the Command Center web UI). See
`README.md` for the full command catalog and Docker layout.

- **CRM direction.** The custom Command Center CRM (Supabase `agency_crm_*`) is
  today's CRM source of truth; RSG is migrating the CRM system of record to **Zoho**.
  Zoho writes are additive and opt-in today (`HERMES_WRITE_TO_ZOHO=1`, using
  `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` / `ZOHO_REFRESH_TOKEN` / `ZOHO_DATA_CENTER`);
  the Command Center CRM will be decommissioned once Zoho is promoted. NowCerts
  stays the AMS system of record and Supabase stays the intelligence layer.

- **Virtualenv.** The dev environment lives in `.venv` (gitignored). Activate it
  before running anything: `source .venv/bin/activate`. The startup update
  script installs both editable packages into this venv, plus `pytest`.
- **Dependency pin gotcha (important).** `pyproject.toml` only sets lower bounds,
  so a plain `pip install -e .` pulls the newest FastAPI (0.141+), whose
  `include_router` stores a lazy wrapper in `app.routes` instead of the route
  objects. That silently breaks the split-service routing in `hermes/services.py`
  and fails `tests/test_services.py`. The update script pins the web stack to the
  committed `poetry.lock` values (`fastapi==0.136.1`, `starlette==1.0.0`,
  `uvicorn==0.46.0`) to keep routing and the suite correct. Don't "upgrade" these
  without re-checking `tests/test_services.py`.
- **Tests.** `pytest` from the repo root (config in `pyproject.toml`; it puts
  `packages/rsg-hermes-core` on the path). No lint/format tooling is configured
  in-repo. The suite is fast (~4s) and needs no external services.
- **Running the backend.** `hermes-api --host 127.0.0.1 --port 8787`. `/health`
  and `GET /api/command-center/skills` work with no database. Most other
  `/api/*` endpoints (carriers, sync-health, dashboards) read Supabase/NowCerts
  and return HTTP 500 until those creds are set — that is expected, not a bug.
- **Credentials.** No secrets are set by default. Live end-to-end work (sync,
  KPIs, `hermes --ops-doctor`, dashboards, LLM one-shots) needs `SUPABASE_URL` +
  `SUPABASE_SERVICE_ROLE_KEY`, `NOWCERTS_*`, and an LLM key added via the Secrets
  panel. `--ops-doctor` fails fast with "SUPABASE_URL ... must be set" when they
  are absent.
- **Deliverable generation is self-contained.** Renewal worksheet PDFs
  (`hermes/renewals/pdf.py`, reportlab) and proposal HTML→PDF
  (`hermes/proposals/generator.py`, WeasyPrint) run without any external service;
  WeasyPrint's native Pango libs are part of the environment.
