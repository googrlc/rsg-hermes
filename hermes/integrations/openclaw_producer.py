"""Hermes → OpenClaw queue producer (public.openclaw_task_queue).

Inserts MUST use server-side Supabase credentials (service role). Never expose
the service role key to browsers or client-side code.

Contract (Manager / Analyst):
  task_type: appetite-analyzer | retention-risk-scout | crm-manager
  payload: JSON object per task_type
  status: PENDING (Postgres sync_status enum)
  priority: integer, 1 = highest; use 1–2 for revenue/renewal-urgent work, default 5
"""

from __future__ import annotations

import logging
import time
from typing import Any, Final

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

OPENCLAW_TASK_TYPES: Final[frozenset[str]] = frozenset(
    {
        "appetite-analyzer",
        "retention-risk-scout",
        "crm-manager",
    }
)

# One initial attempt plus three retries; sleep 1s, 2s, 4s after failures 1–3.
_MAX_ENQUEUE_ATTEMPTS = 4
_BACKOFF_SECONDS: Final[tuple[float, ...]] = (1.0, 2.0, 4.0)


def validate_openclaw_task_payload(task_type: str, payload: dict[str, Any]) -> None:
    """Raise ValueError if payload does not meet the minimal contract."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    tt = task_type.strip()
    if tt not in OPENCLAW_TASK_TYPES:
        raise ValueError(
            f"task_type must be one of: {', '.join(sorted(OPENCLAW_TASK_TYPES))}"
        )

    if tt == "crm-manager":
        if not str(payload.get("client_id", "")).strip():
            raise ValueError("crm-manager payload requires client_id")
    elif tt == "retention-risk-scout":
        if not str(payload.get("client_id", "")).strip():
            raise ValueError("retention-risk-scout payload requires client_id")
    elif tt == "appetite-analyzer":
        naics = str(payload.get("naics_code") or "").strip()
        sic = str(payload.get("sic_code") or "").strip()
        industry = str(payload.get("industry") or "").strip()
        state = str(payload.get("state") or "").strip()
        if not state:
            raise ValueError("appetite-analyzer payload requires state")
        if not (naics or sic or industry):
            raise ValueError(
                "appetite-analyzer payload requires at least one of naics_code, sic_code, industry"
            )


def _insert_openclaw_row(
    supa: SupabaseClient,
    *,
    task_type: str,
    payload: dict[str, Any],
    priority: int,
    requested_by: str,
    notify_slack: bool,
) -> dict[str, Any]:
    """Single PostgREST insert; success requires a returned row id."""
    row = supa.insert(
        "openclaw_task_queue",
        {
            "task_type": task_type.strip(),
            "payload": payload,
            "status": "PENDING",
            "priority": priority,
            "attempt_count": 0,
            "requested_by": requested_by,
            "notify_slack": bool(notify_slack),
        },
    )
    row_id = row.get("id") if isinstance(row, dict) else None
    if not row_id:
        raise SupabaseClientError("openclaw_task_queue insert did not return row id")
    return row


def enqueue_openclaw_task(
    supa: SupabaseClient,
    *,
    task_type: str,
    payload: dict[str, Any],
    requested_by: str = "hermes",
    priority: int = 5,
    notify_slack: bool = False,
) -> dict[str, Any]:
    """Insert into openclaw_task_queue with validation and exponential backoff retries.

    Retries up to 3 times after the first failure (4 attempts total), sleeping
    1s, 2s, then 4s between attempts. Treats success only when PostgREST returns a row id.
    """
    tt = task_type.strip()
    if tt not in OPENCLAW_TASK_TYPES:
        raise ValueError(
            f"task_type must be one of: {', '.join(sorted(OPENCLAW_TASK_TYPES))}"
        )
    if priority < 1:
        raise ValueError("priority must be >= 1")
    validate_openclaw_task_payload(tt, payload)

    last_error: Exception | None = None
    for attempt in range(_MAX_ENQUEUE_ATTEMPTS):
        try:
            return _insert_openclaw_row(
                supa,
                task_type=tt,
                payload=payload,
                priority=priority,
                requested_by=requested_by,
                notify_slack=notify_slack,
            )
        except (SupabaseClientError, ValueError) as exc:
            last_error = exc
            log.warning(
                "openclaw_task_queue enqueue attempt %s/%s failed: %s",
                attempt + 1,
                _MAX_ENQUEUE_ATTEMPTS,
                exc,
            )
            if attempt < len(_BACKOFF_SECONDS):
                time.sleep(_BACKOFF_SECONDS[attempt])

    assert last_error is not None
    raise last_error
