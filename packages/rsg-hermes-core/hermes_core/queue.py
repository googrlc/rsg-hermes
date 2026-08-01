"""The shared contract for ``outbound_sync_queue`` — the one queue every domain
executor drains.

This module exists because the contract had no home. The table name, the status
values, the destination systems, the object-type strings and the backoff filter
were all defined inside ``hermes/renewals/executor.py``, so six unrelated
domains (quotes, intake, casework, command_center, sync, and the scheduler)
imported the *renewal* executor to learn how to talk to a queue that is not
renewal-specific. Renewals looked like the most central module in the codebase
with 21 inbound edges; almost all of them were this.

The queue is shared infrastructure, so its wire protocol belongs in the bottom
layer where every domain can depend on it and none depend on each other. Nothing
here may import a domain — that is the whole point.

The object-type list is deliberately explicit rather than a registry populated by
import side effects: whether a failed job is ever retried must be greppable, not
a function of which modules happened to be imported. See
``BACKED_OFF_OBJECT_TYPES`` and ``tests/test_retry_backoff_coverage.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# --- The table ----------------------------------------------------------------
QUEUE_TABLE = "outbound_sync_queue"

# --- Statuses -----------------------------------------------------------------
QUEUE_QUEUED = "queued"
QUEUE_PROCESSING = "processing"
QUEUE_COMPLETED = "completed"
QUEUE_FAILED = "failed"
QUEUE_DEAD = "dead"

# --- Destination systems ------------------------------------------------------
DESTINATION_NOWCERTS = "nowcerts"
DESTINATION_CRM = "crm"

# --- Object types -------------------------------------------------------------
# One per kind of work the queue carries. These are wire values shared between a
# domain executor and the scheduler, which is why they live here rather than in
# each domain: the scheduler must know them without importing the domains.
OBJECT_TYPE_RENEWAL = "renewal"
OBJECT_TYPE_INTAKE = "intake"
OBJECT_TYPE_QUOTE = "quote"
OBJECT_TYPE_CASE = "case"
OBJECT_TYPE_TASK = "task"
OBJECT_TYPE_OPPORTUNITY_WRITEBACK = "opportunity_writeback"
OBJECT_TYPE_INTAKE_AMS = "intake_ams"
OBJECT_TYPE_INTAKE_CRM = "intake_crm"

# Every object_type whose failures should back off, and every destination whose
# rows the retry pass manages. These two tuples and `due_filter()` are a matched
# set: a type listed here gets `scheduled_for` set on failure, and ONLY an
# executor that applies `due_filter()` will then actually wait.
#
# Listing a type here without its executor honouring the filter means a failing
# job is retried immediately, every scheduler cycle, forever — an exponential
# backoff that silently does nothing. Until 2026-07-26 this held just
# (renewal, intake), which happened to be exactly the two executors that
# honoured it, so the system was accidentally consistent.
# tests/test_retry_backoff_coverage.py guards the pairing.
BACKED_OFF_OBJECT_TYPES = (
    OBJECT_TYPE_RENEWAL,
    OBJECT_TYPE_INTAKE,
    OBJECT_TYPE_QUOTE,
    OBJECT_TYPE_CASE,
    OBJECT_TYPE_TASK,
    OBJECT_TYPE_OPPORTUNITY_WRITEBACK,
    OBJECT_TYPE_INTAKE_AMS,
    OBJECT_TYPE_INTAKE_CRM,
)

BACKED_OFF_DESTINATIONS = (DESTINATION_NOWCERTS, DESTINATION_CRM)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def due_filter(now: datetime | None = None) -> dict[str, str]:
    """The PostgREST filter every executor must apply to respect a backoff.

    One home for it. It existed as a copy-pasted `or=(...)` string in two
    executors and was absent from the other five, so honouring the backoff was a
    property of where you happened to look rather than of the queue.
    """
    stamp = (now or utcnow()).isoformat()
    return {"or": f"(scheduled_for.is.null,scheduled_for.lte.{stamp})"}


def extract_created_id(result: Any) -> str | None:
    """Pull the created task/note id from a NowCerts insert response.

    NowCerts' Zapier InsertTask nests the record under ``data`` (e.g.
    ``result["data"]["database_id"]``), so check BOTH the top level and the
    nested ``data`` object — otherwise a successfully-created task reads as
    "no id" and fails read-after-write verification.
    """
    if not isinstance(result, dict):
        return None
    for obj in (result, result.get("data") if isinstance(result.get("data"), dict) else {}):
        for key in ("database_id", "databaseId", "noteId", "note_id", "id"):
            val = obj.get(key)
            if val:
                return str(val)
    return None
