FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl gcc \
    # WeasyPrint (proposal HTML→PDF) native deps: Pango for text layout + a base
    # font. Pango pulls cairo/harfbuzz/glib/fontconfig transitively.
    libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

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
# [gmail] extra pulls google-auth, needed at runtime by the Gmail email-triage
# lane and the Google Drive document mirror.
RUN pip install -e '.[gmail]'
# Belt-and-suspenders: guarantee the PDF dependency is present + pinned in the image
# (renewal worksheet PDF generation). Verified in CI/rebuild via `python -c import reportlab`.
RUN pip install "reportlab==5.0.0"
# Proposal HTML→PDF rendering. Native Pango libs installed above; verify the
# import at build time so a broken image fails fast rather than at first render.
RUN pip install "weasyprint>=60" && python -c "import weasyprint; print('weasyprint', weasyprint.__version__)"

ENTRYPOINT []
