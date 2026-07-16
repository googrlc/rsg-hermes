FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl gcc && rm -rf /var/lib/apt/lists/*

# Pin poetry <2 so `poetry lock --no-update` works and a pyproject/lock drift
# (e.g. a dep added without a re-lock) self-heals at build time instead of failing.
RUN pip install "poetry>=1.8,<2"

COPY pyproject.toml poetry.lock ./
# Re-lock (no version bumps) so newly-added deps like reportlab are resolved, then
# install the main group.
RUN poetry lock --no-update \
    && poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

COPY . .
# [gmail] extra pulls google-auth, needed at runtime by the Gmail email-triage
# lane and the Google Drive document mirror.
RUN pip install -e '.[gmail]'
# Belt-and-suspenders: guarantee the PDF dependency is present + pinned in the image
# (renewal worksheet PDF generation). Verified in CI/rebuild via `python -c import reportlab`.
RUN pip install "reportlab==5.0.0"

ENTRYPOINT []
