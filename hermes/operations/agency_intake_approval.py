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
import re
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


def _map_account_to_espo(account: dict[str, Any]) -> dict[str, Any]:
    """Translate intake Account fields to EspoCRM Account field names."""
    mapped: dict[str, Any] = {}
    field_map = {
        "account_name": "name",
        "legal_name": "legalName",
        "dba": "dba",
        "fein": "fein",
        "entity_type": "typeOfBusiness",
        "industry": "industry",
        "address": "billingAddressStreet",
        "city": "billingAddressCity",
        "state": "billingAddressState",
        "zip": "billingAddressPostalCode",
        "phone": "phoneNumber",
        "email": "emailAddress",
        "website": "website",
        "operations_summary": "description",
        "annual_revenue": "annualRevenue",
        "estimated_payroll": "estimatedPayroll",
        "employee_count": "employeeCount",
        "account_type": "accountType",
        "account_status": "accountStatus",
    }
    for src, dst in field_map.items():
        val = account.get(src)
        if val is not None:
            mapped[dst] = val
    if mapped.get("phoneNumber"):
        mapped["phoneNumber"] = _normalize_phone_us(mapped["phoneNumber"])
    if account.get("tags"):
        mapped["tags"] = account["tags"]
    return mapped


def _normalize_phone_us(raw: str) -> str:
    """Normalize a US phone to E.164 (`+1XXXXXXXXXX`).

    Live-tested 2026-05-22 against ``POST /api/v1/Contact`` on the rsg-espocrm
    install: of (770) 780-8848, 7707808848, 770-780-8848, 770.780.8848,
    +17707808848 — only the E.164 form returned HTTP 200. Everything else
    returned ``validationFailure {field: phoneNumber, type: valid}``.

    Returns the raw string unchanged if it's not a 10/11-digit US number,
    so non-US or unparsable input flows through for human inspection.
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+1{digits}"
    return raw


# EspoCRM Opportunity.lineOfBusiness allowed values — keep this list in sync
# with the live ``entityDefs.Opportunity.fields.lineOfBusiness.options`` array.
# The field is required, audited, and locked after first save, so a wrong value
# burns a record. Verified 2026-05-22 via Metadata API.
ESPO_LINE_OF_BUSINESS_OPTIONS: tuple[str, ...] = (
    "Commercial Auto",
    "Transportation / Trucking",
    "General Liability",
    "Workers Comp",
    "Commercial Property",
    "BOP",
    "Professional Liability",
    "Umbrella",
    "Builders Risk",
    "Inland Marine",
    "Personal Auto",
    "Homeowners",
    "Renters",
    "Condo",
    "Dwelling Fire",
    "Motorcycle",
    "Boat",
    "RV",
    "Life",
    "Health",
    "Medicare",
    "Group Benefits",
    "Garagekeepers",
    "Commercial Package",
    "Other",
)


# Maps lowercase-stripped LLM-extractor strings to a value in
# ``ESPO_LINE_OF_BUSINESS_OPTIONS``. Unknown values pass through ``_normalize_lob``
# with a logged warning so misses are visible.
_LOB_ALIASES: dict[str, str] = {
    # General Liability family — each variant is a distinct enum entry now.
    "general liability": "General Liability",
    "gl": "General Liability",
    "commercial general liability": "General Liability",
    "cgl": "General Liability",
    # BOP family
    "bop": "BOP",
    "business owners policy": "BOP",
    # Commercial Package family
    "commercial package": "Commercial Package",
    "commercial package liability": "Commercial Package",
    "cpl": "Commercial Package",
    "package": "Commercial Package",
    # Commercial Property family
    "commercial property": "Commercial Property",
    "property": "Commercial Property",
    # Workers Comp
    "workers compensation": "Workers Comp",
    "workers' compensation": "Workers Comp",
    "workers comp": "Workers Comp",
    "wc": "Workers Comp",
    "wc/employers liability": "Workers Comp",
    # Commercial Auto
    "commercial auto": "Commercial Auto",
    "auto": "Commercial Auto",
    "ca": "Commercial Auto",
    # Transportation / Trucking
    "transportation": "Transportation / Trucking",
    "trucking": "Transportation / Trucking",
    "transportation / trucking": "Transportation / Trucking",
    # Inland Marine
    "inland marine": "Inland Marine",
    "im": "Inland Marine",
    # Umbrella
    "umbrella": "Umbrella",
    "commercial umbrella": "Umbrella",
    "excess liability": "Umbrella",
    # Builders Risk
    "builders risk": "Builders Risk",
    # Professional Liability
    "professional liability": "Professional Liability",
    "errors and omissions": "Professional Liability",
    "e&o": "Professional Liability",
    # Garagekeepers
    "garagekeepers": "Garagekeepers",
    # Personal lines — each enum entry stands alone; no "Personal Lines" umbrella.
    "personal auto": "Personal Auto",
    "homeowners": "Homeowners",
    "renters": "Renters",
    "condo": "Condo",
    "dwelling fire": "Dwelling Fire",
    "motorcycle": "Motorcycle",
    "boat": "Boat",
    "rv": "RV",
    # Life / Health / Medicare / Group
    "life": "Life",
    "life insurance": "Life",
    "health": "Health",
    "medicare": "Medicare",
    "group benefits": "Group Benefits",
    "group health": "Group Benefits",
    "benefits": "Group Benefits",
    # Catch-all
    "other": "Other",
}


def _normalize_lob(raw: str | None) -> str | None:
    """Map an extractor LOB string to EspoCRM's option-list value. Logs misses."""
    if not raw:
        return raw
    key = raw.strip().lower()
    mapped = _LOB_ALIASES.get(key)
    if mapped is None:
        log.warning(
            "lineOfBusiness alias not mapped — sending raw value, EspoCRM may reject: %r",
            raw,
        )
        return raw
    return mapped


def _map_contact_to_espo(contact: dict[str, Any]) -> dict[str, Any]:
    """Translate intake Contact fields to EspoCRM Contact field names."""
    mapped: dict[str, Any] = {}
    field_map = {
        "first_name": "firstName",
        "last_name": "lastName",
        "phone": "phoneNumber",
        "email": "emailAddress",
        "role": "title",
        "relationship_to_account": "description",
    }
    for src, dst in field_map.items():
        val = contact.get(src)
        if val is not None:
            mapped[dst] = val
    if mapped.get("phoneNumber"):
        mapped["phoneNumber"] = _normalize_phone_us(mapped["phoneNumber"])
    return mapped


def _map_opportunity_to_espo(opp: dict[str, Any]) -> dict[str, Any]:
    """Translate intake Opportunity fields to EspoCRM Opportunity field names."""
    mapped: dict[str, Any] = {}
    field_map = {
        "opportunity_name": "name",
        "line_of_business": "lineOfBusiness",
        "stage": "stage",
        "quote_number": "quoteNumber",
        "carrier": "carrier",
        "premium": "amount",
        "proposed_effective_date": "proposedEffectiveDate",
        "opportunity_type": "opportunityType",
        "producer": "producer",
        "package_name": "packageName",
    }
    for src, dst in field_map.items():
        val = opp.get(src)
        if val is not None:
            mapped[dst] = val
    if mapped.get("lineOfBusiness"):
        mapped["lineOfBusiness"] = _normalize_lob(mapped["lineOfBusiness"])
    # EspoCRM requires closeDate — use proposed_effective_date
    if opp.get("proposed_effective_date"):
        mapped["closeDate"] = opp["proposed_effective_date"]
    if opp.get("tags"):
        mapped["tags"] = opp["tags"]
    return mapped


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
        espo_account = _map_account_to_espo(account)
        row = enqueue_crm_write(
            supa,
            entity_type="Account",
            payload=espo_account,
            created_by_role=created_by_role,
            priority=1,
        )
        queue_ids.append(str(row.get("id")))
        plan["steps"].append({"order": 1, "entity": "Account", "queue_id": row.get("id")})

    account_name = account.get("account_name") or ""

    for idx, contact in enumerate(contacts, start=1):
        if not contact:
            continue
        espo_contact = _map_contact_to_espo(contact)
        if account_name:
            espo_contact["accountName"] = account_name
        row = enqueue_crm_write(
            supa,
            entity_type="Contact",
            payload=espo_contact,
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
        espo_opp = _map_opportunity_to_espo(opp)
        if account_name:
            espo_opp["accountName"] = account_name
        row = enqueue_crm_write(
            supa,
            entity_type="Opportunity",
            payload=espo_opp,
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
            "content": note.get("body"),
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
    """Apply an approval token to an intake_submissions row.

    Phase 3 rewrite: the source of truth is now ``intake_submissions``
    (keyed by submission_id) rather than ``agency_intake_drafts`` (keyed
    by draft_id). The ``draft_id`` keyword is kept so existing callers in
    api.py and slack_socket.py don't need touching — but the value is
    now interpreted as a submission_id (UUID of intake_submissions.id).

    Behavior:
      APPROVE ALL                → transition awaiting_approval -> approved
                                   (worker picks up, enqueues CRM writes,
                                   walks writing -> written -> complete)
      APPROVE CRM ONLY           → same as APPROVE ALL for now; worker
                                   handles both paths uniformly. The
                                   token is preserved in status_history.
      APPROVE SUPABASE ONLY      → ditto
      APPROVE TASKS ONLY         → ditto
      REVISE                     → transition to failed with note
                                   "marked revised by approver"
                                   (intake_submissions enum has no
                                   'revised' state)
      CANCEL                     → transition to failed with note
                                   "canceled by approver"
    """
    from datetime import datetime, timezone

    from hermes.integrations.intake_submissions import (
        IntakeError,
        fetch_by_id,
        transition,
    )

    submission_id = draft_id  # parameter is named draft_id for back-compat
    token = (token or "").strip().upper()
    if token not in ALLOWED_APPROVAL_TOKENS:
        raise ApprovalError(
            f"Token {token!r} is not allowed. Use one of: {sorted(ALLOWED_APPROVAL_TOKENS)}"
        )

    submission = fetch_by_id(supa, submission_id)
    if submission is None:
        raise ApprovalError(
            f"Submission {submission_id} not found in intake_submissions. "
            f"This Slack message may be stale from the pre-Phase-3 agency_intake_drafts path."
        )

    current_status = submission.get("status")
    if current_status != "awaiting_approval":
        raise ApprovalError(
            f"Submission {submission_id} is in status={current_status!r}, "
            f"not 'awaiting_approval' — cannot apply approval token."
        )

    approver_label = approver or "system"

    if token == "CANCEL":
        try:
            transition(
                supa, submission_id, "failed",
                note=f"canceled by {approver_label}",
                error={"reason": "canceled by approver", "token": token},
            )
        except IntakeError as exc:
            raise ApprovalError(f"Cancel transition failed: {exc}") from exc
        return ApprovalResult(
            ok=True, draft_id=submission_id, token=token, status="failed",
            summary=f"Submission {submission_id} canceled. Nothing was written.",
        )

    if token == "REVISE":
        try:
            transition(
                supa, submission_id, "failed",
                note=f"marked revised by {approver_label}",
                error={"reason": "marked revised by approver", "token": token},
            )
        except IntakeError as exc:
            raise ApprovalError(f"Revise transition failed: {exc}") from exc
        return ApprovalResult(
            ok=True, draft_id=submission_id, token=token, status="failed",
            summary=f"Submission {submission_id} marked failed (revise). Resubmit with corrections.",
        )

    # Any APPROVE* token → transition to 'approved'. The worker handles
    # the rest. Partial-scope tokens (CRM ONLY / SUPABASE ONLY / TASKS
    # ONLY) currently behave identically to APPROVE ALL — the token is
    # preserved in status_history for audit + future scope control.
    approved_at_iso = datetime.now(timezone.utc).isoformat()
    try:
        transition(
            supa, submission_id, "approved",
            note=f"{token} by {approver_label}",
            extra_fields={
                "approved_by": approver_label,
                "approved_at": approved_at_iso,
            },
        )
    except IntakeError as exc:
        raise ApprovalError(f"Approve transition failed: {exc}") from exc

    return ApprovalResult(
        ok=True,
        draft_id=submission_id,
        token=token,
        status="approved",
        summary=(
            f"Submission {submission_id} approved via {token}. "
            f"Worker will enqueue CRM writes and walk through writing -> written -> complete."
        ),
    )
