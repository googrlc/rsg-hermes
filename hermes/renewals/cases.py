"""Renewal case + task persistence (Supabase-native renewal workspace).

A *case* is the working container for one renewal event, keyed on the same
event identity the eligibility engine uses (insured + policy lineage + event
date). *Tasks* are the actionable steps under a case. Both are idempotent:
re-running create for the same event / same task title returns the existing row
rather than duplicating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

CASES_TABLE = "renewal_cases"
TASKS_TABLE = "renewal_tasks"

STATUS_OPEN = "open"

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


def default_tasks(assigned_to: str | None = None) -> list[dict[str, Any]]:
    return [
        {"title": title, "detail": detail, "assigned_to": assigned_to, "source": "default_template"}
        for title, detail in DEFAULT_TASK_TEMPLATES
    ]


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
    assigned_to: str | None = None,
    summary: str | None = None,
    created_by: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Idempotent create. Returns ``(case_row, created)`` — created=False if it existed."""
    if not (insured_id and policy_lineage_id and renewal_event_date):
        raise ValueError("insured_id, policy_lineage_id, and renewal_event_date are required")

    existing = supa.select(
        CASES_TABLE,
        columns="*",
        params={
            "insured_id": f"eq.{insured_id}",
            "policy_lineage_id": f"eq.{policy_lineage_id}",
            "renewal_event_date": f"eq.{renewal_event_date}",
        },
        limit=1,
    )
    if existing:
        return existing[0], False

    row = supa.insert(
        CASES_TABLE,
        {
            "insured_id": insured_id,
            "policy_lineage_id": policy_lineage_id,
            "renewal_event_date": renewal_event_date,
            "policy_number": policy_number,
            "nowcerts_policy_guid": nowcerts_policy_guid,
            "client_name": client_name,
            "line_of_business": line_of_business,
            "segment": segment,
            "status": STATUS_OPEN,
            "assigned_to": assigned_to,
            "summary": summary,
            "created_by": created_by,
        },
    )
    return row, True


def get_case_by_policy(supa: "SupabaseClient", policy_number: str) -> dict[str, Any] | None:
    rows = supa.select(
        CASES_TABLE, columns="*", params={"policy_number": f"eq.{policy_number}"}, limit=1
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
    default_assignee: str | None = None,
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
            {
                "case_id": case_id,
                "title": title,
                "detail": t.get("detail"),
                "assigned_to": t.get("assigned_to") or default_assignee,
                "due_date": t.get("due_date"),
                "status": STATUS_OPEN,
                "source": t.get("source") or "manual",
            },
        )
        created.append(row)
        existing.add(title)
    return created
