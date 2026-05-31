FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl gcc && rm -rf /var/lib/apt/lists/*

RUN pip install poetry

COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

COPY . .
# [gmail] extra pulls google-auth, needed at runtime by the Gmail email-triage
# lane and the Google Drive document mirror.
RUN pip install -e '.[gmail]'

ENTRYPOINT []
