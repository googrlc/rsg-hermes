# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY hermes ./hermes

RUN pip install --no-cache-dir -e .

CMD ["hermes", "--slack"]
