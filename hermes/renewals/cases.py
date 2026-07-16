"""Renewal case + task persistence on the SHARED agency CRM schema (#113).

Renewal cases live in ``agency_crm_cases`` (``case_type='renewal'``) alongside
marketing/service/claims/etc., with tasks in ``agency_crm_tasks``, documents in
``agency_crm_document_links``, and the renewal-event identity + renewal-only
attributes in the 1:1 ``renewal_case_details`` table. This replaces the separate
renewal_cases/renewal_tasks tables PR #107 introduced (which were never applied
to prod — no backfill).

Idempotency is keyed on the renewal-event identity (insured + policy lineage +
event date) so repeated Hermes commands return the same case.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

CASES_TABLE = "agency_crm_cases"
TASKS_TABLE = "agency_crm_tasks"
DETAILS_TABLE = "renewal_case_details"
DOCLINKS_TABLE = "agency_crm_document_links"

CASE_TYPE_RENEWAL = "renewal"
CASE_STATUS_OPEN = "open"           # agency_crm_cases.status vocabulary
TASK_STATUS_OPEN = "not_started"    # agency_crm_tasks.status vocabulary
PRIORITY_DEFAULT = "medium"

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


def _service_email() -> str:
    return os.environ.get("HERMES_SERVICE_EMAIL", "hermes@risk-solutionsgroup.com")


def _default_owner_email() -> str | None:
    return os.environ.get("HERMES_RENEWAL_OWNER_EMAIL") or None


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop None values so we never send blank keys that could clobber shared rows."""
    return {k: v for k, v in payload.items() if v is not None}


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


def _case_by_id(supa: "SupabaseClient", case_id: str) -> dict[str, Any] | None:
    rows = supa.select(CASES_TABLE, columns="*", params={"id": f"eq.{case_id}"}, limit=1)
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
    return case, True


def get_case_by_policy(supa: "SupabaseClient", policy_number: str) -> dict[str, Any] | None:
    rows = supa.select(
        CASES_TABLE,
        columns="*",
        params={"case_type": f"eq.{CASE_TYPE_RENEWAL}", "policy_number": f"eq.{policy_number}"},
        limit=1,
    )
    return rows[0] if rows else None


def list_tasks(supa: "SupabaseClient", case_id: str) -> list[dict[str, Any]]:
    return supa.select(
        TASKS_TABLE, columns="*", params={"case_id": f"eq.{case_id}", "order": "created_at.asc"}, limit=200
    )


def create_tasks(
    supa: "SupabaseClient",
    *,
    case_id: str,
    tasks: list[dict[str, Any]],
    default_assignee_email: str | None = None,
    created_by_email: str | None = None,
) -> list[dict[str, Any]]:
    """Insert tasks under a case, skipping titles that already exist (idempotent)."""
    existing = {t.get("title") for t in list_tasks(supa, case_id)}
    created: list[dict[str, Any]] = []
    for t in tasks:
        title = (t.get("title") or "").strip()
        if not title or title in existing:
            continue
        row = supa.insert(
            TASKS_TABLE,
            _compact({
                "case_id": case_id,
                "title": title,
                "description": t.get("description") or t.get("detail"),
                "status": TASK_STATUS_OPEN,
                "priority": PRIORITY_DEFAULT,
                "assigned_to_email": t.get("assigned_to_email") or default_assignee_email,
                "created_by_email": created_by_email or _service_email(),
            }),
        )
        created.append(row)
        existing.add(title)
    return created


def link_document(
    supa: "SupabaseClient",
    *,
    case_id: str,
    title: str,
    nextcloud_path: str,
    nextcloud_url: str | None = None,
    insured_id: str | None = None,
    content_type: str | None = None,
    uploaded_by_email: str | None = None,
) -> dict[str, Any]:
    """Link a filed document to a case via the shared agency_crm_document_links table."""
    return supa.insert(
        DOCLINKS_TABLE,
        _compact({
            "case_id": case_id,
            "insured_database_id": insured_id,
            "title": title,
            "nextcloud_path": nextcloud_path,
            "nextcloud_url": nextcloud_url,
            "content_type": content_type,
            "uploaded_by_email": uploaded_by_email or _service_email(),
        }),
    )
