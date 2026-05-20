"""Shared approval logic for agency intake drafts.

Both the Slack interactive button handler and the
`POST /agency-intake/approve` HTTP endpoint call `approve_draft()` here.

Flow when token = APPROVE ALL:
  1. Load the draft row from `agency_intake_drafts`.
  2. Build a write plan from the payload (Account → Contacts → per-LOB
     Opportunities → ClientNote).
  3. Enqueue one `crm_write_queue` row per CRM entity. The existing
     `hermes-crm-queue-worker` picks them up and POSTs to EspoCRM.
  4. Insert retrieval rows: one `client_entities` row per Account/Contact,
     then `client_facts` from the draft's `facts[]`, then a `client_notes` row.
  5. Update the draft status + write_plan + enqueued_queue_ids + retrieval_row_ids.

Partial-scope tokens behave like a subset:
  APPROVE CRM ONLY      — skip retrieval inserts
  APPROVE SUPABASE ONLY — skip CRM queue, do retrieval inserts
  APPROVE TASKS ONLY    — (reserved for future Task entries)
  REVISE                — mark revised, return early
  CANCEL                — mark canceled, return early
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hermes.commands.agency_intake import ALLOWED_APPROVAL_TOKENS
from hermes.integrations import retrieval_client
from hermes.operations.crm_queue_worker import enqueue_crm_write

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)


@dataclass
class ApprovalResult:
    ok: bool
    draft_id: str
    token: str
    status: str
    enqueued_queue_ids: list[str] = field(default_factory=list)
    retrieval_row_ids: dict[str, list[str]] = field(default_factory=dict)
    summary: str = ""
    error: str | None = None


class ApprovalError(Exception):
    pass


def _load_draft(supa: "SupabaseClient", draft_id: str) -> dict[str, Any]:
    rows = supa.select(
        "agency_intake_drafts",
        params={"id": f"eq.{draft_id}"},
        limit=1,
    )
    if not rows:
        raise ApprovalError(f"Draft {draft_id} not found")
    return rows[0]


def _update_draft_status(
    supa: "SupabaseClient",
    draft_id: str,
    *,
    status: str,
    approval_token: str,
    approved_by: str | None,
    write_plan: dict[str, Any] | None = None,
    enqueued_queue_ids: list[str] | None = None,
    retrieval_row_ids: dict[str, list[str]] | None = None,
    error: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "approval_token": approval_token,
        "approved_by": approved_by,
        "approved_at": "now()",
    }
    if write_plan is not None:
        payload["write_plan"] = write_plan
    if enqueued_queue_ids is not None:
        payload["enqueued_queue_ids"] = enqueued_queue_ids
    if retrieval_row_ids is not None:
        payload["retrieval_row_ids"] = retrieval_row_ids
    if error is not None:
        payload["error"] = error
    # PostgREST does not parse "now()" — drop it and let the table default / trigger handle it
    payload.pop("approved_at", None)
    supa.update("agency_intake_drafts", draft_id, payload)


def _enqueue_crm_writes(
    supa: "SupabaseClient",
    payload: dict[str, Any],
    *,
    created_by_role: str,
) -> tuple[list[str], dict[str, Any]]:
    """Enqueue Account → Contacts → per-LOB Opportunities → ClientNote.

    Returns (queue_ids, write_plan_snapshot).
    """
    queue_ids: list[str] = []
    plan: dict[str, Any] = {"steps": []}

    account = payload.get("account") or {}
    contacts = payload.get("contacts") or []
    opps = payload.get("opportunities") or []
    note = payload.get("note") or {}

    if account.get("account_name"):
        row = enqueue_crm_write(
            supa,
            entity_type="Account",
            payload=account,
            created_by_role=created_by_role,
            priority=1,
        )
        queue_ids.append(str(row.get("id")))
        plan["steps"].append({"order": 1, "entity": "Account", "queue_id": row.get("id")})

    for idx, contact in enumerate(contacts, start=1):
        if not contact:
            continue
        row = enqueue_crm_write(
            supa,
            entity_type="Contact",
            payload=contact,
            created_by_role=created_by_role,
            priority=2,
        )
        queue_ids.append(str(row.get("id")))
        plan["steps"].append(
            {"order": 1 + idx, "entity": "Contact", "queue_id": row.get("id")}
        )

    for idx, opp in enumerate(opps, start=1):
        if not opp.get("opportunity_name") or not opp.get("line_of_business"):
            log.warning("Skipping opportunity without name/LOB: %s", opp)
            continue
        row = enqueue_crm_write(
            supa,
            entity_type="Opportunity",
            payload=opp,
            created_by_role=created_by_role,
            priority=3,
        )
        queue_ids.append(str(row.get("id")))
        plan["steps"].append(
            {
                "order": 100 + idx,
                "entity": "Opportunity",
                "lob": opp.get("line_of_business"),
                "queue_id": row.get("id"),
            }
        )

    if note.get("body") and note.get("title"):
        note_payload = {
            "name": note.get("title"),
            "description": note.get("body"),
            "noteType": note.get("note_type"),
            "tags": note.get("tags") or [],
            "parentType": "Account",
            "parentName": account.get("account_name"),
        }
        row = enqueue_crm_write(
            supa,
            entity_type="ClientNote",
            payload=note_payload,
            created_by_role=created_by_role,
            priority=4,
        )
        queue_ids.append(str(row.get("id")))
        plan["steps"].append({"order": 999, "entity": "ClientNote", "queue_id": row.get("id")})

    return queue_ids, plan


def _insert_retrieval_rows(
    supa: "SupabaseClient",
    payload: dict[str, Any],
) -> dict[str, list[str]]:
    """Insert client_entities + client_facts + client_notes rows.

    CRM ids land later (via crm_receipts) — these rows are written eagerly
    so retrieval works even before the CRM POST completes.
    """
    out: dict[str, list[str]] = {"client_entities": [], "client_facts": [], "client_notes": []}

    account = payload.get("account") or {}
    contacts = payload.get("contacts") or []
    note = payload.get("note") or {}
    facts = payload.get("facts") or []

    entity_id_lookup: dict[str, str] = {}

    if account.get("account_name"):
        entity_row = retrieval_client.upsert_entity(
            supa,
            entity_type="Account",
            entity_name=account["account_name"],
            canonical_aliases=[account["account_name"], account.get("legal_name"), account.get("dba")],
            tags=account.get("tags") or [],
        )
        entity_id = str(entity_row.get("id") or "")
        if entity_id:
            entity_id_lookup[account["account_name"]] = entity_id
            out["client_entities"].append(entity_id)
            primary_account_entity_id = entity_id
        else:
            primary_account_entity_id = None
    else:
        primary_account_entity_id = None

    for contact in contacts:
        name = contact.get("full_name") or f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
        if not name:
            continue
        entity_row = retrieval_client.upsert_entity(
            supa,
            entity_type="Contact",
            entity_name=name,
            canonical_aliases=[name],
            primary_account_entity_id=primary_account_entity_id,
        )
        entity_id = str(entity_row.get("id") or "")
        if entity_id:
            entity_id_lookup[name] = entity_id
            out["client_entities"].append(entity_id)

    fact_rows = retrieval_client.bulk_insert_facts(
        supa,
        facts=[f for f in facts if isinstance(f, dict)],
        entity_id_lookup=entity_id_lookup,
    )
    out["client_facts"] = [str(r.get("id")) for r in fact_rows if r.get("id")]

    if note.get("title") and note.get("body") and primary_account_entity_id:
        note_row = retrieval_client.insert_note(
            supa,
            entity_id=primary_account_entity_id,
            note_type=note.get("note_type") or "Underwriting Summary",
            title=note["title"],
            summary=(note["body"][:300] + "…") if len(note["body"]) > 300 else note["body"],
            full_text=note["body"],
            audience=note.get("audience") or "internal",
            sensitivity=note.get("sensitivity") or "standard",
            tags=note.get("tags") or [],
            author=(payload.get("source") or {}).get("submitted_by"),
            note_date=(payload.get("source") or {}).get("date"),
            source=(payload.get("source") or {}).get("type"),
            source_ref=(payload.get("source") or {}).get("source_ref"),
        )
        if note_row.get("id"):
            out["client_notes"].append(str(note_row["id"]))

    return out


def approve_draft(
    supa: "SupabaseClient",
    *,
    draft_id: str,
    token: str,
    approver: str | None,
) -> ApprovalResult:
    """Apply the approval token to the staged draft."""
    token = (token or "").strip().upper()
    if token not in ALLOWED_APPROVAL_TOKENS:
        raise ApprovalError(
            f"Token {token!r} is not allowed. Use one of: {sorted(ALLOWED_APPROVAL_TOKENS)}"
        )

    draft = _load_draft(supa, draft_id)
    if draft.get("status") not in {"pending"}:
        raise ApprovalError(
            f"Draft {draft_id} is in status={draft.get('status')!r}, not 'pending'"
        )

    payload = draft.get("payload") or {}

    if token == "CANCEL":
        _update_draft_status(
            supa, draft_id,
            status="canceled", approval_token=token, approved_by=approver,
        )
        return ApprovalResult(
            ok=True, draft_id=draft_id, token=token, status="canceled",
            summary="Draft canceled. Nothing was written.",
        )

    if token == "REVISE":
        _update_draft_status(
            supa, draft_id,
            status="revised", approval_token=token, approved_by=approver,
        )
        return ApprovalResult(
            ok=True, draft_id=draft_id, token=token, status="revised",
            summary="Draft marked revised. Resubmit with the corrections.",
        )

    do_crm = token in {"APPROVE ALL", "APPROVE CRM ONLY"}
    do_supabase = token in {"APPROVE ALL", "APPROVE SUPABASE ONLY"}
    role = f"agency-intake:{approver or 'system'}"

    queue_ids: list[str] = []
    write_plan: dict[str, Any] = {"steps": [], "token": token}
    retrieval_ids: dict[str, list[str]] = {}

    if do_crm:
        queue_ids, write_plan = _enqueue_crm_writes(
            supa, payload, created_by_role=role
        )
    if do_supabase:
        retrieval_ids = _insert_retrieval_rows(supa, payload)

    final_status = (
        "approved"
        if token == "APPROVE ALL"
        else "partially_approved"
    )

    _update_draft_status(
        supa, draft_id,
        status=final_status,
        approval_token=token,
        approved_by=approver,
        write_plan=write_plan,
        enqueued_queue_ids=queue_ids,
        retrieval_row_ids=retrieval_ids,
    )

    summary_parts = [
        f"Draft {draft_id} {final_status} via {token}.",
    ]
    if queue_ids:
        summary_parts.append(
            f"Enqueued {len(queue_ids)} CRM writes (worker will POST to EspoCRM)."
        )
    if retrieval_ids:
        counts = ", ".join(f"{k}={len(v)}" for k, v in retrieval_ids.items() if v)
        if counts:
            summary_parts.append(f"Retrieval inserts: {counts}.")

    return ApprovalResult(
        ok=True,
        draft_id=draft_id,
        token=token,
        status=final_status,
        enqueued_queue_ids=queue_ids,
        retrieval_row_ids=retrieval_ids,
        summary=" ".join(summary_parts),
    )
