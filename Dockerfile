FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl gcc \
    # WeasyPrint (proposal HTML→PDF) native deps: Pango for text layout + a base
    # font. Pango pulls cairo/harfbuzz/glib/fontconfig transitively.
    libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# The container runs as a non-root user with no writable HOME, so fontconfig
# has nowhere to cache — point its cache at a world-writable dir so WeasyPrint
# renders fast and quietly instead of re-scanning fonts on every PDF.
ENV XDG_CACHE_HOME=/tmp/.cache
RUN mkdir -p /tmp/.cache/fontconfig && chmod -R 1777 /tmp/.cache

# Poetry 2.x — it reads the PEP 621 [project] table this pyproject uses
# (poetry 1.x needs [tool.poetry] and cannot lock this file).
RUN pip install poetry

COPY pyproject.toml poetry.lock ./
# Refresh the lock so deps added to pyproject without a re-lock (e.g. reportlab)
# are resolved — otherwise `poetry install` fails on pyproject/lock drift.
RUN poetry lock \
    && poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

COPY . .
# The shared bottom layer first — the app imports hermes_core / hermes_integrations
# at module scope, so it must be installed before the app is.
RUN pip install -e './packages/rsg-hermes-core'
RUN pip install -e '.'
# `pyproject.toml` only sets lower bounds, so `pip install -e .` pulls FastAPI
# 0.141+ and `include_router` stores lazy wrappers. That breaks split-service
# routing in hermes/services.py. Pin to poetry.lock (same as .cursor/install.sh).
RUN pip install "fastapi==0.136.1" "starlette==1.0.0" "uvicorn==0.46.0" \
    && python -c "import fastapi, starlette, uvicorn; print('fastapi', fastapi.__version__, 'starlette', starlette.__version__, 'uvicorn', uvicorn.__version__)"
# Belt-and-suspenders: guarantee the PDF dependency is present + pinned in the image
# (renewal worksheet PDF generation). Verified in CI/rebuild via `python -c import reportlab`.
RUN pip install "reportlab==5.0.0"
# Proposal HTML→PDF rendering. Native Pango libs installed above; verify the
# import at build time so a broken image fails fast rather than at first render.
RUN pip install "weasyprint>=60" && python -c "import weasyprint; print('weasyprint', weasyprint.__version__)"

ENTRYPOINT []
