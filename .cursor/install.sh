#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for rsg-hermes.
#
# Creates/refreshes the .venv virtualenv, installs the two editable packages
# (rsg-hermes-core first, then the rsg-hermes app), plus pytest, and pins the
# web stack to the committed poetry.lock values. The pin matters: pyproject only
# sets lower bounds, so a plain editable install pulls the newest FastAPI whose
# include_router stores a lazy wrapper in app.routes and silently breaks the
# split-service routing in hermes/services.py (see AGENTS.md + tests/test_services.py).
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
VENV="$REPO_ROOT/.venv"

# Prefer python3.11 if present (the app targets 3.11+); fall back to python3.
if command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
else
  PY=python3
fi

if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

python -m pip install --upgrade pip

# Shared bottom layer first: the app imports hermes_core / hermes_app /
# hermes_integrations at module scope, so it must be importable before the app.
pip install -e "$REPO_ROOT/packages/rsg-hermes-core"
pip install -e "$REPO_ROOT"

# Test tooling (not runtime dependencies, so not in pyproject). httpx is what
# starlette/fastapi's TestClient imports; the runtime `openai` dep only pulls
# httpx2, so the suite needs httpx installed explicitly. Pinned to the
# poetry.lock value for reproducibility.
pip install pytest "httpx==0.28.1"

# Pin the web stack to the committed poetry.lock values so the split-service
# routing in hermes/services.py keeps working. Do not bump without re-checking
# tests/test_services.py.
pip install "fastapi==0.136.1" "starlette==1.0.0" "uvicorn==0.46.0"

echo "hermes dev environment ready:"
python -c "import fastapi, starlette, uvicorn; print('fastapi', fastapi.__version__, '| starlette', starlette.__version__, '| uvicorn', uvicorn.__version__)"
