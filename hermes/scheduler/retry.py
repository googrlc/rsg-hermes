"""Bounded exponential retry/backoff, dead-letter, and stalled-job reclaim for the
NowCerts executor queue (outbound_sync_queue).

The scheduler runs this BEFORE each executor pass:
  * failed jobs under the attempt cap are re-queued with exponential backoff
    (status failed -> queued, scheduled_for = now + backoff);
  * failed jobs at/over the cap are dead-lettered (status -> 'dead') and surfaced
    for alerting;
  * jobs stuck in 'processing' past a threshold (a crashed executor) are reclaimed
    back to 'queued' and surfaced.

Executors honor ``scheduled_for`` so backed-off jobs wait their turn.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from hermes.intake.commit import OBJECT_TYPE_INTAKE
from hermes.casework.executor import OBJECT_TYPE_CASE, OBJECT_TYPE_TASK
from hermes.command_center.router import OBJECT_TYPE_AMS as OBJECT_TYPE_INTAKE_AMS
from hermes.quotes.executor import OBJECT_TYPE_QUOTE
from hermes.renewals.executor import DESTINATION_NOWCERTS, OBJECT_TYPE_RENEWAL, QUEUE_TABLE
from hermes.sync.opportunity_writeback import OBJECT_TYPE as OBJECT_TYPE_OPPORTUNITY_WRITEBACK

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
BACKOFF_FACTOR = 2
STALLED_PROCESSING_SECONDS = 900  # 15 min stuck in 'processing' => crashed executor

QUEUE_QUEUED = "queued"
QUEUE_PROCESSING = "processing"
QUEUE_FAILED = "failed"
QUEUE_DEAD = "dead"

# Every object_type whose failures should back off. This tuple and
# `due_filter()` below are a matched pair: a type listed here gets scheduled_for
# set on failure, and ONLY an executor that honours scheduled_for will then wait.
#
# Until 2026-07-26 this held just (renewal, intake) — which happened to be exactly
# the two executors that honour it, so the system was accidentally consistent.
# Adding a type here without its executor honouring the column means a failing job
# is retried immediately, every scheduler cycle, forever: an exponential backoff
# that silently does nothing. test_retry_backoff_is_honoured_everywhere guards it.
_OBJECT_TYPES = (
    OBJECT_TYPE_RENEWAL,
    OBJECT_TYPE_INTAKE,
    OBJECT_TYPE_QUOTE,
    OBJECT_TYPE_CASE,
    OBJECT_TYPE_TASK,
    OBJECT_TYPE_OPPORTUNITY_WRITEBACK,
    OBJECT_TYPE_INTAKE_AMS,
)
# intake_crm is deliberately absent. requeue_or_deadletter filters
# destination_system = 'nowcerts', and a CRM-destination job carries
# destination_system = 'crm' — listing it here would look like coverage while the
# destination filter silently excluded every row. CRM-destination failures are not
# backed off at ALL today; fixing that means broadening the destination filter,
# which is a separate change with its own blast radius.


def due_filter(now: datetime | None = None) -> dict[str, str]:
    """The PostgREST filter every executor must apply to respect a backoff.

    One home for it. It existed as a copy-pasted `or=(...)` string in two
    executors and was absent from the other five, so honouring the backoff was a
    property of where you happened to look rather than of the queue.
    """
    stamp = (now or _utcnow()).isoformat()
    return {"or": f"(scheduled_for.is.null,scheduled_for.lte.{stamp})"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_backoff_seconds(attempt: int) -> int:
    """Exponential backoff for the Nth retry (1-based), capped."""
    exp = BACKOFF_BASE_SECONDS * (BACKOFF_FACTOR ** max(0, attempt - 1))
    return int(min(BACKOFF_CAP_SECONDS, exp))


def requeue_or_deadletter(supa: "SupabaseClient", *, now: datetime | None = None) -> dict[str, Any]:
    """Re-queue failed NowCerts jobs with backoff, or dead-letter past the cap.

    Returns metrics incl. the exact dead-lettered queue ids (for alerting/audit).
    """
    now = now or _utcnow()
    try:
        failed = supa.select(
            QUEUE_TABLE,
            columns="id,object_type,object_id,attempt_count",
            params={
                "object_type": f"in.({','.join(_OBJECT_TYPES)})",
                "destination_system": f"eq.{DESTINATION_NOWCERTS}",
                "status": f"eq.{QUEUE_FAILED}",
                "order": "created_at.asc",
            },
            limit=200,
        )
    except Exception:
        log.exception("requeue_or_deadletter: select failed")
        return {"requeued": 0, "dead": 0, "dead_ids": []}

    requeued = 0
    dead_ids: list[str] = []
    for row in failed:
        qid = row.get("id")
        attempt = int(row.get("attempt_count") or 0)
        next_attempt = attempt + 1
        try:
            if next_attempt >= MAX_ATTEMPTS:
                supa.update(QUEUE_TABLE, qid, {"status": QUEUE_DEAD, "updated_at": now.isoformat()})
                dead_ids.append(str(qid))
            else:
                delay = compute_backoff_seconds(next_attempt)
                supa.update(QUEUE_TABLE, qid, {
                    "status": QUEUE_QUEUED,
                    "attempt_count": next_attempt,
                    "scheduled_for": (now + timedelta(seconds=delay)).isoformat(),
                    "updated_at": now.isoformat(),
                })
                requeued += 1
        except Exception:
            log.exception("requeue_or_deadletter: update failed for queue_id=%s", qid)
    return {"requeued": requeued, "dead": len(dead_ids), "dead_ids": dead_ids}


def reclaim_stalled(
    supa: "SupabaseClient", *, now: datetime | None = None, threshold_seconds: int = STALLED_PROCESSING_SECONDS
) -> dict[str, Any]:
    """Reset jobs stuck in 'processing' past the threshold back to 'queued'.

    Returns the reclaimed ids (a crashed/killed executor left them claimed).
    """
    now = now or _utcnow()
    cutoff = (now - timedelta(seconds=threshold_seconds)).isoformat()
    try:
        stalled = supa.select(
            QUEUE_TABLE,
            columns="id,object_type,updated_at",
            params={
                "object_type": f"in.({','.join(_OBJECT_TYPES)})",
                "destination_system": f"eq.{DESTINATION_NOWCERTS}",
                "status": f"eq.{QUEUE_PROCESSING}",
                "updated_at": f"lt.{cutoff}",
            },
            limit=200,
        )
    except Exception:
        log.exception("reclaim_stalled: select failed")
        return {"reclaimed": 0, "reclaimed_ids": []}

    reclaimed_ids: list[str] = []
    for row in stalled:
        qid = row.get("id")
        try:
            supa.update(QUEUE_TABLE, qid, {"status": QUEUE_QUEUED, "updated_at": now.isoformat()})
            reclaimed_ids.append(str(qid))
        except Exception:
            log.exception("reclaim_stalled: update failed for queue_id=%s", qid)
    return {"reclaimed": len(reclaimed_ids), "reclaimed_ids": reclaimed_ids}
