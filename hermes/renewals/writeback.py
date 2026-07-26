"""Two-phase, approval-gated NowCerts writeback staging.

This is the *staging* half of the renewal writeback contract; the actual AMS
mutation is performed out-of-band by the Renewal Executor
(``hermes.renewals.executor``), which does the mandatory read-before /
verify / read-after write and records a ``renewal_execution_receipts`` row.

    propose_writeback(...)  -> inserts an UNAPPROVED ``outbound_sync_queue`` row
                               (approved_by / approved_at = NULL). Nothing
                               reaches NowCerts. The row is inert.
    list_pending(...)       -> the proposed-but-unapproved rows (the review set).
    confirm_writeback(...)  -> stamps approved_by + approved_at, which flips the
                               row into the executor's eligible set.

Only the approval-gated executors on the Hermes scheduler consume
``destination_system='nowcerts'`` rows, so a proposed row waits safely for a
human to confirm before anything reaches the AMS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes.renewals.executor import (
    ACTION_UPDATE_AMS,
    ACTIONS,
    DESTINATION_NOWCERTS,
    OBJECT_TYPE_RENEWAL,
    QUEUE_QUEUED,
    QUEUE_TABLE,
)

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def propose_writeback(
    supa: "SupabaseClient",
    *,
    action: str,
    policy_number: str,
    expected_result: str,
    renewal_id: str | None = None,
    fields: dict[str, Any] | None = None,
    channel: str = "task",
    note: str | None = None,
    proposed_by: str | None = None,
) -> dict[str, Any]:
    """Stage an UNAPPROVED NowCerts writeback. Returns the inserted queue row.

    No approval is stamped — ``confirm_writeback`` is required before the executor
    will touch it. ``update_ams`` requires ``fields`` (the NowCerts PartialUpdate
    values); the task/note actions use ``note``.
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown renewal action '{action}'; must be one of {sorted(ACTIONS)}")
    if not policy_number:
        raise ValueError("policy_number is required")
    if action == ACTION_UPDATE_AMS and not fields:
        raise ValueError("update_ams requires fields to write")

    queue_action = "update" if action == ACTION_UPDATE_AMS else "create"
    payload: dict[str, Any] = {
        "action": action,
        "renewal_id": renewal_id,
        "policy_number": policy_number,
        "expected_result": expected_result,
        "channel": channel,
    }
    if fields:
        payload["fields"] = fields
    if note:
        payload["note"] = note
    if proposed_by:
        payload["proposed_by"] = proposed_by

    return supa.insert(
        QUEUE_TABLE,
        {
            "object_type": OBJECT_TYPE_RENEWAL,
            "object_id": policy_number,
            "destination_system": DESTINATION_NOWCERTS,
            "action": queue_action,
            "payload": payload,
            "status": QUEUE_QUEUED,
            "attempt_count": 0,
            "approved_by": None,   # UNAPPROVED until confirm_writeback
            "approved_at": None,
        },
    )


def list_pending(
    supa: "SupabaseClient", *, policy_number: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Proposed-but-unapproved NowCerts renewal rows (the human review set).

    Deliberately does NOT apply retry.due_filter(): this lists rows awaiting a
    human decision (approved_at is null), it does not claim work. Hiding a
    backed-off row from the review list would make it invisible to the person who
    needs to approve or cancel it.
    """
    params: dict[str, str] = {
        "object_type": f"eq.{OBJECT_TYPE_RENEWAL}",
        "destination_system": f"eq.{DESTINATION_NOWCERTS}",
        "status": f"eq.{QUEUE_QUEUED}",
        "approved_at": "is.null",
        "order": "created_at.asc",
    }
    if policy_number:
        params["object_id"] = f"eq.{policy_number}"
    return supa.select(QUEUE_TABLE, params=params, limit=limit)


def confirm_writeback(
    supa: "SupabaseClient", *, queue_id: str, approved_by: str
) -> list[dict[str, Any]]:
    """Approve one proposed row → eligible for the executor.

    Guarded: only flips a row that is still ``queued`` AND unapproved, so a
    double-confirm or a race can't re-approve or disturb an in-flight job.
    Returns the updated row(s) (empty if nothing matched).
    """
    now = _utcnow_iso()
    return supa.update_where(
        QUEUE_TABLE,
        {"approved_by": approved_by, "approved_at": now, "updated_at": now},
        filters={
            "id": f"eq.{queue_id}",
            "status": f"eq.{QUEUE_QUEUED}",
            "approved_at": "is.null",
        },
    )


def confirm_pending_for_policy(
    supa: "SupabaseClient", *, policy_number: str, approved_by: str
) -> list[dict[str, Any]]:
    """Approve every proposed row for one exact policy number. Returns the rows updated."""
    now = _utcnow_iso()
    return supa.update_where(
        QUEUE_TABLE,
        {"approved_by": approved_by, "approved_at": now, "updated_at": now},
        filters={
            "object_type": f"eq.{OBJECT_TYPE_RENEWAL}",
            "destination_system": f"eq.{DESTINATION_NOWCERTS}",
            "object_id": f"eq.{policy_number}",
            "status": f"eq.{QUEUE_QUEUED}",
            "approved_at": "is.null",
        },
    )
