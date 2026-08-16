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

# System dependencies. The default base image ships python3 but not the venv
# package (no ensurepip), and WeasyPrint (proposal HTML->PDF) needs native Pango
# libs + a base font. Install only what's missing so idempotent re-runs stay
# fast. Kept here rather than in a Dockerfile so the repo-managed environment
# works from the default image with no extra base config.
SYS_PKGS=(python3-venv python3-dev build-essential \
  libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core shared-mime-info)
missing=()
for pkg in "${SYS_PKGS[@]}"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ "${#missing[@]}" -gt 0 ]; then
  if [ "$(id -u)" -eq 0 ]; then SUDO=""; elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else SUDO=""; fi
  $SUDO apt-get update -qq
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
fi

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
