"""intake_submissions table writer.

Phase 2 of the rsg-intake pipeline: accept submission, idempotency-check
against ``intake_submissions.idempotency_key``, insert row with
``status='received'``. The async worker (Phase 3) drives the state machine
from there.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

TABLE = "intake_submissions"

_UNIQUE_VIOLATION_HINTS = ("23505", "duplicate key", "idempotency_key")


class IntakeError(Exception):
    """Raised when an intake submission cannot be persisted."""


def _is_unique_violation(exc: SupabaseClientError) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _UNIQUE_VIOLATION_HINTS)


def _isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        raise IntakeError("captured_at must include a timezone offset")
    return value.isoformat()


def fetch_by_idempotency_key(supa: SupabaseClient, idempotency_key: str) -> dict[str, Any] | None:
    rows = supa.select(
        TABLE,
        params={"idempotency_key": f"eq.{idempotency_key}"},
        limit=1,
    )
    return rows[0] if rows else None


def insert_submission(
    supa: SupabaseClient,
    *,
    idempotency_key: str,
    source: str,
    agent: str,
    intake_kind: str,
    client_identifier: str | None,
    lob_code: str | None,
    captured_at: datetime,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Insert one submission row. Returns (row, is_new).

    ``is_new`` is False when an existing row with the same idempotency_key
    is returned instead of a fresh insert.
    """
    row_payload = {
        "idempotency_key": idempotency_key,
        "source": source,
        "agent": agent,
        "intake_kind": intake_kind,
        "client_identifier": client_identifier,
        "lob_code": lob_code,
        "captured_at": _isoformat(captured_at),
        "payload": payload,
    }
    try:
        row = supa.insert(TABLE, row_payload)
        return row, True
    except SupabaseClientError as exc:
        if not _is_unique_violation(exc):
            raise
        existing = fetch_by_idempotency_key(supa, idempotency_key)
        if existing is None:
            # Unique-violation but no row found — surface the original error.
            raise IntakeError(
                "idempotency_key conflict but no existing row could be located"
            ) from exc
        return existing, False
