"""CRM change proposals — staged EspoCRM field updates awaiting approval.

This is the in-chat approval surface for *direct* CRM field edits (a VIN add,
an account_status fix, a single-field correction) as opposed to full intakes
(which go through ``intake_submissions``). It deliberately does NOT bypass the
review gate or write to EspoCRM directly:

    agent proposes  ->  crm_change_proposals row (status=pending)
    reviewer approves from chat
        ->  one crm_write_queue row is enqueued
        ->  the running hermes-crm-queue-worker commits it to EspoCRM
            (hooks/ACL/Stream all fire because the write goes through EspoClient)

So the "committer" is the existing crm-queue-worker; this module is the staging +
approval layer in front of it. ``after`` must use EspoCRM field names — the
proposer is responsible for loading the espocrm field-reference skill first
(per AGENTS.md).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

ALLOWED_OPS = frozenset({"upsert", "create", "update"})
UPDATE_OPS = frozenset({"upsert", "update"})  # these require an espocrm_id


class ProposalError(Exception):
    """Carries an HTTP-ish status code for the API layer."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _validate(
    *,
    entity: str,
    op: str,
    after: dict[str, Any],
    espocrm_id: str | None,
) -> None:
    if not entity or not str(entity).strip():
        raise ProposalError("entity is required (e.g. Account, Contact, Opportunity)")
    if op not in ALLOWED_OPS:
        raise ProposalError(f"op must be one of {sorted(ALLOWED_OPS)}, got {op!r}")
    if not isinstance(after, dict) or not after:
        raise ProposalError("after must be a non-empty dict of EspoCRM field values")
    if op in UPDATE_OPS and not espocrm_id:
        raise ProposalError(f"op={op!r} requires espocrm_id (cannot update without a target)")
    if op == "create" and espocrm_id:
        raise ProposalError("op='create' must not set espocrm_id (create has no existing target)")


def create_proposal(
    supa: SupabaseClient,
    *,
    entity: str,
    after: dict[str, Any],
    op: str = "upsert",
    match_key: str | None = None,
    espocrm_id: str | None = None,
    before: dict[str, Any] | None = None,
    rationale: str | None = None,
    confidence: float | None = None,
    source: str | None = None,
    proposed_by: str = "agent",
) -> dict[str, Any]:
    """Insert one pending proposal row. Returns the created row."""
    _validate(entity=entity, op=op, after=after, espocrm_id=espocrm_id)
    row = {
        "entity": entity,
        "op": op,
        "match_key": match_key,
        "espocrm_id": espocrm_id,
        "before": before or {},
        "after": after,
        "rationale": rationale,
        "confidence": confidence,
        "source": source,
        "status": "pending",
        "proposed_by": proposed_by or "agent",
    }
    try:
        return supa.insert("crm_change_proposals", row)
    except SupabaseClientError as exc:
        raise ProposalError(f"insert failed: {exc}", 502) from exc


def get_proposal(supa: SupabaseClient, proposal_id: str) -> dict[str, Any] | None:
    rows = supa.select(
        "crm_change_proposals",
        params={"id": f"eq.{proposal_id}"},
        limit=1,
    )
    return rows[0] if rows else None


def list_proposals(
    supa: SupabaseClient, *, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"order": "created_at.desc"}
    if status:
        params["status"] = f"eq.{status}"
    return supa.select("crm_change_proposals", params=params, limit=limit)


def approve_proposal(
    supa: SupabaseClient,
    proposal_id: str,
    *,
    reviewer: str,
) -> dict[str, Any]:
    """Approve a pending proposal: enqueue a crm_write_queue row and mark approved.

    The hermes-crm-queue-worker commits the enqueued row to EspoCRM asynchronously.
    This function never writes to EspoCRM directly.
    """
    from hermes.operations.crm_queue_worker import enqueue_crm_write

    proposal = get_proposal(supa, proposal_id)
    if proposal is None:
        raise ProposalError(f"proposal {proposal_id} not found", 404)
    if proposal.get("status") != "pending":
        raise ProposalError(
            f"proposal {proposal_id} is status={proposal.get('status')!r}, not 'pending'",
            409,
        )

    entity = proposal["entity"]
    espocrm_id = proposal.get("espocrm_id")
    after = proposal.get("after") or {}
    op = proposal.get("op", "upsert")
    # Re-validate in case the row was written out-of-band with a bad shape.
    _validate(entity=entity, op=op, after=after if isinstance(after, dict) else {}, espocrm_id=espocrm_id)

    try:
        queue_row = enqueue_crm_write(
            supa,
            entity_type=entity,
            entity_id=espocrm_id,
            payload=after,
            created_by_role="reviewer",
            priority=1,
        )
    except Exception as exc:
        # Mark the proposal failed so it isn't silently re-approvable.
        supa.update(
            "crm_change_proposals",
            proposal_id,
            {"status": "failed", "reviewed_by": reviewer, "error": f"enqueue failed: {exc}"},
        )
        raise ProposalError(f"enqueue failed: {exc}", 502) from exc

    queue_id = str(queue_row.get("id"))
    supa.update(
        "crm_change_proposals",
        proposal_id,
        {
            "status": "approved",
            "reviewed_by": reviewer,
            "result": {"queue_id": queue_id, "committed_via": "crm_write_queue"},
        },
    )
    return {
        "proposal_id": proposal_id,
        "status": "approved",
        "queue_id": queue_id,
        "entity": entity,
        "espocrm_id": espocrm_id,
        "message": (
            f"Approved. Enqueued crm_write_queue row {queue_id}; "
            "the hermes-crm-queue-worker will commit it to EspoCRM."
        ),
    }


def reject_proposal(
    supa: SupabaseClient,
    proposal_id: str,
    *,
    reviewer: str,
    reason: str | None = None,
) -> dict[str, Any]:
    proposal = get_proposal(supa, proposal_id)
    if proposal is None:
        raise ProposalError(f"proposal {proposal_id} not found", 404)
    if proposal.get("status") not in ("pending", "approved"):
        raise ProposalError(
            f"proposal {proposal_id} is status={proposal.get('status')!r}; "
            "only pending (or not-yet-committed approved) can be rejected",
            409,
        )
    supa.update(
        "crm_change_proposals",
        proposal_id,
        {"status": "rejected", "reviewed_by": reviewer, "error": reason},
    )
    return {"proposal_id": proposal_id, "status": "rejected", "reviewer": reviewer}
