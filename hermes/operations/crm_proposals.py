"""CRM change proposals — staged EspoCRM field updates awaiting approval.

This is the in-chat approval surface for *direct* CRM field edits (a VIN add,
an account_status fix, a single-field correction) as opposed to full intakes
(which go through ``intake_submissions``). It deliberately does NOT bypass the
review gate or write to EspoCRM directly:

    agent proposes  ->  crm_change_proposals row (status=pending)
    reviewer approves from chat
        ->  for most entities: one crm_write_queue row is enqueued and the
            running hermes-crm-queue-worker commits it to EspoCRM
        ->  for OpportunityDriver / OpportunityVehicle: approve commits via
            EspoClient directly, with scoped dedup on license number / VIN and
            the create-then-link step for Lead-attached records (see below)

``after`` must use EspoCRM field names — the proposer is responsible for loading
the espocrm field-reference skill first (per AGENTS.md).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

if TYPE_CHECKING:
    from hermes.core.client import EspoClient

log = logging.getLogger(__name__)

ALLOWED_OPS = frozenset({"upsert", "create", "update"})
UPDATE_OPS = frozenset({"upsert", "update"})  # these require an espocrm_id

# Driver/Vehicle get a dedicated approve path: scoped dedup + Lead create-then-link.
DRIVER_VEHICLE_ENTITIES = frozenset({"OpportunityDriver", "OpportunityVehicle"})
DEDUP_FIELD = {"OpportunityDriver": "driverLicenseNumber", "OpportunityVehicle": "vin"}
LEAD_LINK_NAME = {"OpportunityDriver": "opportunityDrivers", "OpportunityVehicle": "opportunityVehicles"}
# Parent FK fields, in priority order, used to scope dedup and to choose the link step.
_PARENT_FIELDS = ("opportunityId", "accountId", "leadId")


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
    if op == "update" and not espocrm_id:
        raise ProposalError("op='update' requires espocrm_id (cannot update without a target)")
    # op='upsert' without espocrm_id is allowed — the worker treats it as a
    # create (same entity_id=None path as op='create').
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


# ---------------------------------------------------------------------------
# Driver / Vehicle approve path
# ---------------------------------------------------------------------------

def _resolve_parent(after: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (parent_field, parent_id) for the first present parent FK."""
    for f in _PARENT_FIELDS:
        v = after.get(f)
        if v:
            return f, str(v)
    return None, None


def _scoped_find(
    espo: "EspoClient",
    entity: str,
    dedup_field: str,
    key: str,
    parent_field: str,
    parent_id: str,
) -> str | None:
    """Find an existing record matching the dedup key scoped to the same parent."""
    body = espo.get(
        entity,
        params={
            "maxSize": 1,
            "select": "id,name",
            "where": [
                {"type": "equals", "attribute": dedup_field, "value": key},
                {"type": "equals", "attribute": parent_field, "value": parent_id},
            ],
        },
    )
    rows = body.get("list") if isinstance(body, dict) else None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        rid = rows[0].get("id")
        return str(rid) if rid else None
    return None


def _mark_committed(
    supa: SupabaseClient,
    proposal_id: str,
    reviewer: str,
    espocrm_id: str,
    action: str,
    entity: str,
) -> None:
    supa.update(
        "crm_change_proposals",
        proposal_id,
        {
            "status": "committed",
            "reviewed_by": reviewer,
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "result": {"espocrm_id": espocrm_id, "action": action, "entity": entity},
        },
    )


def _mark_failed(supa: SupabaseClient, proposal_id: str, reviewer: str, err: str) -> None:
    supa.update(
        "crm_change_proposals",
        proposal_id,
        {"status": "failed", "reviewed_by": reviewer, "error": err[:1000]},
    )


def _approve_driver_vehicle(
    supa: SupabaseClient,
    espo: "EspoClient",
    proposal: dict[str, Any],
    reviewer: str,
) -> dict[str, Any]:
    """Commit a Driver/Vehicle proposal directly via EspoClient.

    Dedup: a create with a license number (driver) / VIN (vehicle) AND a parent
    FK first looks for an existing record on the SAME parent with that key; if
    found, it updates that record instead of creating a duplicate. Dedup is
    scoped to the parent (a person can legitimately be a driver on two policies,
    so a global match would wrongly merge them).

    Lead-attached create: inline ``leadId`` on create is ACL-blocked by EspoCRM,
    so the record is created without it and then linked to the Lead via the
    relationship endpoint (``POST /Lead/{id}/opportunityDrivers``).
    """
    entity = proposal["entity"]
    after = dict(proposal.get("after") or {})
    op = proposal.get("op", "create")
    record_id = proposal.get("espocrm_id")

    try:
        if op == "update":
            if not record_id:
                raise ProposalError("op='update' requires espocrm_id", 400)
            espo.update(entity, str(record_id), after)
            _mark_committed(supa, str(proposal["id"]), reviewer, str(record_id), "update", entity)
            return _result(str(proposal["id"]), "committed", entity, str(record_id), "update",
                           f"Updated {entity} {record_id} in EspoCRM.")

        # op == "create" (upsert treated as create): scoped dedup, then create-or-update
        dedup_field = DEDUP_FIELD[entity]
        parent_field, parent_id = _resolve_parent(after)
        key_val = str(after.get(dedup_field) or "").strip()

        existing_id: str | None = None
        if key_val and parent_field and parent_id:
            existing_id = _scoped_find(espo, entity, dedup_field, key_val, parent_field, parent_id)
        elif key_val and not parent_field:
            # No parent specified: fall back to a global match (rare).
            hit = espo.find_one_by_field(entity, dedup_field, key_val, select="id,name")
            existing_id = str(hit["id"]) if isinstance(hit, dict) and hit.get("id") else None

        if existing_id:
            espo.update(entity, existing_id, after)
            _mark_committed(supa, str(proposal["id"]), reviewer, existing_id, "update+dedup", entity)
            return _result(str(proposal["id"]), "committed", entity, existing_id, "update+dedup",
                           f"Linked to existing {entity} {existing_id} (dedup on {dedup_field}); updated.")

        # No duplicate: create. Inline leadId is ACL-blocked, so strip it and link after.
        lead_id = after.pop("leadId", None)
        created = espo.create(entity, after)
        new_id = str((created or {}).get("id") or "") if isinstance(created, dict) else ""
        if not new_id:
            raise ProposalError(f"{entity} create returned no id", 502)

        action = "create"
        opp_id = after.get("opportunityId")
        acct_id = after.get("accountId")
        if lead_id and not opp_id and not acct_id:
            espo.post(f"Lead/{lead_id}/{LEAD_LINK_NAME[entity]}", {"id": new_id})
            action = "create+link"

        _mark_committed(supa, str(proposal["id"]), reviewer, new_id, action, entity)
        return _result(str(proposal["id"]), "committed", entity, new_id, action,
                       f"Committed to EspoCRM: {action} {entity} {new_id}.")
    except ProposalError:
        raise
    except Exception as exc:
        _mark_failed(supa, str(proposal["id"]), reviewer, str(exc))
        raise ProposalError(f"commit failed: {exc}", 502) from exc


def _result(proposal_id: str, status: str, entity: str, espocrm_id: str, action: str, message: str) -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "status": status,
        "entity": entity,
        "espocrm_id": espocrm_id,
        "action": action,
        "message": message,
    }


def approve_proposal(
    supa: SupabaseClient,
    proposal_id: str,
    *,
    reviewer: str,
    espo: "EspoClient | None" = None,
) -> dict[str, Any]:
    """Approve a pending proposal.

    Driver/Vehicle proposals commit directly via EspoClient (scoped dedup +
    Lead create-then-link). All other entities enqueue a ``crm_write_queue``
    row that the hermes-crm-queue-worker commits asynchronously.
    """
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
    _validate(entity=entity, op=op, after=after if isinstance(after, dict) else {}, espocrm_id=espocrm_id)

    if entity in DRIVER_VEHICLE_ENTITIES:
        if espo is None:
            raise ProposalError("EspoClient is required to approve Driver/Vehicle proposals", 503)
        return _approve_driver_vehicle(supa, espo, proposal, reviewer)

    # Generic path: enqueue one crm_write_queue row, worker commits async.
    from hermes.operations.crm_queue_worker import enqueue_crm_write

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
