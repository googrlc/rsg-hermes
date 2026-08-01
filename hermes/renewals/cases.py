"""The renewal flavour of a case.

What makes a renewal case a renewal: its identity (insured + policy lineage +
event date), the idempotency that identity buys — a repeated Hermes command
returns the same case rather than opening a second one — and the 90/60/30 task
set the desk works through.

Everything generic about cases and tasks lives in ``hermes/casework/store.py``
and is re-exported below, because callers on this side have always reached for
them here and the split should not be their problem.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from hermes.casework.store import (
    CASE_CHILD_TABLES,
    CASE_NUMBER_PREFIX_LEN,
    CASE_STATUS_OPEN,
    CASES_TABLE,
    DETAILS_TABLE,
    DOCLINKS_TABLE,
    EVENTS_TABLE,
    PRIORITY_DEFAULT,
    TASK_PRIORITIES,
    TASK_STATUS_CLOSED,
    TASK_STATUS_OPEN,
    TASK_STATUSES,
    TASKS_TABLE,
    _case_by_id,
    _compact,
    _service_email,
    _slug,
    case_number,
    create_tasks,
    delete_case,
    delete_task,
    get_task,
    link_document,
    list_tasks,
    log_case_event,
    update_case,
    update_task,
)

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient

CASE_TYPE_RENEWAL = "renewal"

# Standard renewal task set (mirrors the 90/60/30 renewal cadence + worksheet).
DEFAULT_TASK_TEMPLATES: list[tuple[str, str]] = [
    ("Pull renewal declaration & review exposures",
     "Retrieve the expiring dec page and confirm current exposures on the worksheet."),
    ("Request renewal terms from carrier",
     "Request renewal terms from the incumbent carrier (remarket if flagged)."),
    ("Prepare renewal options / comparison",
     "Build the option comparison and premium-change explanation for the client."),
    ("Send renewal review to client",
     "Deliver the renewal review and confirm the client's intent."),
    ("Update AMS (NowCerts) & file worksheet",
     "Stage the approved NowCerts write-back and file the worksheet in the client folder."),
]

def _default_owner_email() -> str:
    # owner_email is required + FK'd to agency_crm_users — default to the renewal CSR.
    return os.environ.get("HERMES_RENEWAL_OWNER_EMAIL") or "gretchen@risksolutionsgroup.net"

def renewal_case_number(
    policy_number: str | None, policy_lineage_id: str, renewal_event_date: str
) -> str:
    """The renewal flavour: identity is the policy, dated by the renewal event.

    Kept as its own name because the renewal desk's idempotency is keyed on it
    and the argument order is part of that contract.
    """
    return case_number(
        CASE_TYPE_RENEWAL,
        identity=policy_number or policy_lineage_id or "UNKNOWN",
        on=str(renewal_event_date),
    )

def default_tasks(assigned_to_email: str | None = None) -> list[dict[str, Any]]:
    return [
        {"title": title, "description": detail, "assigned_to_email": assigned_to_email}
        for title, detail in DEFAULT_TASK_TEMPLATES
    ]

def _details_for_identity(
    supa: "SupabaseClient", insured_id: str, policy_lineage_id: str, renewal_event_date: str
) -> dict[str, Any] | None:
    rows = supa.select(
        DETAILS_TABLE,
        columns="*",
        params={
            "insured_id": f"eq.{insured_id}",
            "policy_lineage_id": f"eq.{policy_lineage_id}",
            "renewal_event_date": f"eq.{renewal_event_date}",
        },
        limit=1,
    )
    return rows[0] if rows else None

def create_case(
    supa: "SupabaseClient",
    *,
    insured_id: str,
    policy_lineage_id: str,
    renewal_event_date: str,
    policy_number: str | None = None,
    nowcerts_policy_guid: str | None = None,
    client_name: str | None = None,
    line_of_business: str | None = None,
    segment: str | None = None,
    owner_email: str | None = None,
    created_by_email: str | None = None,
    nextcloud_folder_url: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Idempotent create on the shared agency_crm_cases table. Returns ``(case, created)``.

    Idempotency key is the renewal-event identity in renewal_case_details.
    """
    if not (insured_id and policy_lineage_id and renewal_event_date):
        raise ValueError("insured_id, policy_lineage_id, and renewal_event_date are required")

    existing = _details_for_identity(supa, insured_id, policy_lineage_id, renewal_event_date)
    if existing:
        case = _case_by_id(supa, str(existing.get("case_id")))
        if case:
            return case, False

    case = supa.insert(
        CASES_TABLE,
        _compact({
            "case_type": CASE_TYPE_RENEWAL,
            # Required, no DB default — Hermes generates it deterministically.
            "case_number": renewal_case_number(policy_number, policy_lineage_id, renewal_event_date),
            "title": f"Renewal — {client_name or policy_number or 'client'}",
            "description": f"Renewal review for policy {policy_number}" if policy_number else "Renewal review",
            "status": CASE_STATUS_OPEN,
            "priority": PRIORITY_DEFAULT,
            "insured_database_id": insured_id,
            "insured_name": client_name,
            "policy_number": policy_number,
            "nextcloud_folder_url": nextcloud_folder_url,
            "owner_email": owner_email or _default_owner_email(),
            "created_by_email": created_by_email or _service_email(),
        }),
    )
    supa.insert(
        DETAILS_TABLE,
        _compact({
            "case_id": case.get("id"),
            "insured_id": insured_id,
            "policy_lineage_id": policy_lineage_id,
            "renewal_event_date": renewal_event_date,
            "nowcerts_policy_guid": nowcerts_policy_guid,
            "line_of_business": line_of_business,
            "segment": segment,
        }),
    )
    log_case_event(
        supa,
        case_id=str(case.get("id")),
        event_type="case_created",
        summary=f"Renewal case opened for policy {policy_number or policy_lineage_id}",
        details={"case_type": CASE_TYPE_RENEWAL, "insured_id": insured_id,
                 "renewal_event_date": renewal_event_date},
        actor_email=created_by_email or _service_email(),
    )
    return case, True

def get_case_by_policy(supa: "SupabaseClient", policy_number: str) -> dict[str, Any] | None:
    rows = supa.select(
        CASES_TABLE,
        columns="*",
        params={"case_type": f"eq.{CASE_TYPE_RENEWAL}", "policy_number": f"eq.{policy_number}"},
        limit=1,
    )
    return rows[0] if rows else None
