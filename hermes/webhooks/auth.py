"""Shared-secret gate for service webhooks (Zoho CRM buttons, etc.)."""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request


def _expected_secret() -> str:
    return (os.environ.get("SERVICE_WEBHOOK_SECRET") or "").strip()


def require_service_webhook_secret(request: Request) -> None:
    """Bearer or X-Webhook-Secret must match SERVICE_WEBHOOK_SECRET."""
    expected = _expected_secret()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="SERVICE_WEBHOOK_SECRET is not configured on this server",
        )

    candidates: list[str] = []
    auth = (request.headers.get("authorization") or request.headers.get("Authorization") or "").strip()
    if auth:
        scheme, _, token = auth.partition(" ")
        if scheme.lower() in ("bearer", "token") and token:
            candidates.append(token.strip())
        else:
            candidates.append(auth)
    for header in ("x-webhook-secret", "x-api-key", "api-key"):
        value = (request.headers.get(header) or "").strip()
        if value:
            candidates.append(value)

    if not any(hmac.compare_digest(c, expected) for c in candidates):
        raise HTTPException(status_code=401, detail="invalid or missing webhook secret")
