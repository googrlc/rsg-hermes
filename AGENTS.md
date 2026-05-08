# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

Hermes (`rsg-hermes`) is a Python CLI and Slack bot coordinator for EspoCRM. See `README.md` for the full command reference and setup guide.

### Development environment

- Python >=3.11 with a virtualenv at `.venv/`. Activate with `source .venv/bin/activate`.
- Dependencies are declared in `pyproject.toml`; install with `pip install -e .` (editable mode). There is no `requirements.txt` or lock file.
- The `hermes` CLI entry point is installed by the editable install.

### Running tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

All 42 tests use `unittest` with mocks — no external services (EspoCRM, Supabase, Slack, OpenAI) are required.

### Running the CLI

Most CLI commands require `ESPO_URL` and `ESPO_API_KEY` environment variables (see `.env.example`). Without them, the CLI exits with code 2 and a clear error message. Supabase-only commands (`--ops-doctor`, `--snapshot-kpis`) require `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.

To verify the install without external credentials: `hermes -h`.

### External services

| Service | Required env vars | Notes |
|---------|------------------|-------|
| EspoCRM | `ESPO_URL`, `ESPO_API_KEY` | Needed for most commands |
| Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Operations Center features |
| Slack | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Only for `--slack` mode |
| OpenAI | `OPENAI_API_KEY` or `HERMES_OPENAI_API_KEY` | Optional NLP intent fallback |

### Gotchas

- There is no linter or formatter configured in this project. No `ruff`, `flake8`, `black`, or `mypy` config exists.
- The project uses Python `>=3.11` but the VM has Python 3.12 — this works fine.
- The `.venv` directory is in `.gitignore`. The `python3.12-venv` system package must be installed for `python3 -m venv` to work.
