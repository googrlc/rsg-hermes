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
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

CASES_TABLE = "agency_crm_cases"
TASKS_TABLE = "agency_crm_tasks"
DETAILS_TABLE = "renewal_case_details"
DOCLINKS_TABLE = "agency_crm_document_links"
EVENTS_TABLE = "agency_crm_case_events"

CASE_TYPE_RENEWAL = "renewal"
CASE_STATUS_OPEN = "open"           # agency_crm_cases.status vocabulary
TASK_STATUS_OPEN = "not_started"    # agency_crm_tasks.status vocabulary
# The full vocabulary, mirroring the agency_crm_tasks_status_check constraint.
# Kept here so a write can be validated before Postgres rejects it — a 400 that
# names the valid values beats a 502 wrapping a constraint violation.
TASK_STATUSES = ("not_started", "in_progress", "waiting", "completed", "cancelled")
TASK_STATUS_CLOSED = ("completed", "cancelled")
TASK_PRIORITIES = ("low", "medium", "high", "urgent")
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
    # created_by_email / actor_email are FK'd to agency_crm_users — must be a real
    # user. lc-rsg@ IS the service account (role 'service'): it was already the
    # machine identity, created_by on 5 tasks and assigned_to on zero, it just wore
    # Lamar's display name until 2026-07-26. Defaulting here means a machine write
    # no longer looks like something Lamar did by hand.
    return os.environ.get("HERMES_SERVICE_EMAIL", "lc-rsg@risksolutionsgroup.net")


def _default_owner_email() -> str:
    # owner_email is required + FK'd to agency_crm_users — default to the renewal CSR.
    return os.environ.get("HERMES_RENEWAL_OWNER_EMAIL") or "gretchen@risksolutionsgroup.net"


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop None values so we never send blank keys that could clobber shared rows."""
    return {k: v for k, v in payload.items() if v is not None}


def log_case_event(
    supa: "SupabaseClient",
    *,
    case_id: str,
    event_type: str,
    summary: str,
    details: dict[str, Any] | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Append a row to the shared agency_crm_case_events timeline (best-effort audit)."""
    return supa.insert(
        EVENTS_TABLE,
        _compact({
            "case_id": case_id,
            "event_type": event_type,
            "summary": summary,
            "details": details,
            "actor_email": actor_email or _service_email(),
        }),
    )


# ── Case numbers — one rule ──────────────────────────────────────────────────
# Every case gets a human-readable number, because a case is the thing people
# talk about: it goes in emails, in Talk messages, in a NowCerts note. A uuid
# cannot be read down the phone.
#
# Every case is `TYP-` then two parts, and WHICH two says what kind of case it is:
#
#   * `REN-9300232193-20261029` — a case with a natural identity. A renewal is
#     one policy renewing on one date, so the number is DERIVED from exactly
#     that, and the same identity always yields the same number. That is what
#     makes the renewal desk safe to re-run: a retry lands on the existing case
#     instead of opening a second one. Any case that can name what makes it
#     itself should be numbered this way.
#   * `SER-20260723-7D674A` — a case with none. An ad-hoc service case is just a
#     thing someone opened; two identical requests genuinely ARE two cases, so
#     it is dated and given a random tail.
#
# Read the middle: a policy number means the case is tied to something and is
# idempotent; a date means somebody opened it by hand.
#
# One generator, because this was previously spelled out in three places — the
# renewal helper plus two endpoints that each re-derived it from `utcnow()`,
# which is how they drifted apart and how the generic ones ended up dated in UTC.
CASE_NUMBER_PREFIX_LEN = 3
_IDENTITY_MAX = 24


def _slug(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).upper()


def case_number(
    case_type: str | None,
    *,
    identity: str | None = None,
    on: str | None = None,
    now: "datetime | None" = None,
) -> str:
    """Build a case number — see the rule above.

    ``identity`` is the thing that makes this case THIS case (a policy number, a
    lineage id); pass it and the number is deterministic. ``on`` dates the number
    — a renewal is dated by its event, not by when someone got round to opening
    it — and defaults to today, agency time (not UTC: a case opened at 8pm ET
    used to be numbered with tomorrow's date).
    """
    import uuid

    from hermes.core.due_dates import agency_today

    prefix = _slug(case_type)[:CASE_NUMBER_PREFIX_LEN] or "CAS"
    day = str(on or "")[:10].replace("-", "") or agency_today(now).strftime("%Y%m%d")
    if identity:
        return f"{prefix}-{_slug(identity)[:_IDENTITY_MAX] or 'UNKNOWN'}-{day}"
    return f"{prefix}-{day}-{uuid.uuid4().hex[:6].upper()}"


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


def list_tasks(supa: "SupabaseClient", case_id: str) -> list[dict[str, Any]]:
    return supa.select(
        TASKS_TABLE, columns="*", params={"case_id": f"eq.{case_id}", "order": "created_at.asc"}, limit=200
    )


def _open_titles_in_scope(
    supa: "SupabaseClient",
    *,
    case_id: str | None,
    insured_id: str | None,
) -> set[str]:
    """Titles of OPEN tasks in the same scope, for idempotent create.

    Scope is the parent: a case, else a client, else the internal bucket. And
    only OPEN tasks count — "update commission percentage" is a thing that
    legitimately recurs, and a completed one from last month must not block this
    month's. Deduping on all-time titles would make a real task silently vanish.
    """
    if case_id:
        return {t.get("title") for t in list_tasks(supa, case_id)}

    params: dict[str, str] = {"status": f"not.in.({','.join(TASK_STATUS_CLOSED)})"}
    if insured_id:
        params["insured_database_id"] = f"eq.{insured_id}"
        params["case_id"] = "is.null"
    else:
        params["case_id"] = "is.null"
        params["insured_database_id"] = "is.null"
    try:
        rows = supa.select(TASKS_TABLE, columns="title", params=params, limit=500)
    except Exception:  # noqa: BLE001 — a dedupe read must not block a create
        return set()
    return {r.get("title") for r in rows}


def create_tasks(
    supa: "SupabaseClient",
    *,
    case_id: str | None = None,
    tasks: list[dict[str, Any]],
    default_assignee_email: str | None = None,
    created_by_email: str | None = None,
    insured_database_id: str | None = None,
) -> list[dict[str, Any]]:
    """Insert tasks, skipping titles already open in the same scope (idempotent).

    ``case_id`` is optional as of issue #195: a task may be case work, client
    work with no case, or purely internal.
    """
    existing = _open_titles_in_scope(supa, case_id=case_id, insured_id=insured_database_id)
    created: list[dict[str, Any]] = []
    for t in tasks:
        title = (t.get("title") or "").strip()
        if not title or title in existing:
            continue
        row = supa.insert(
            TASKS_TABLE,
            _compact({
                "case_id": case_id,
                "insured_database_id": insured_database_id,
                "title": title,
                "description": t.get("description") or t.get("detail"),
                "status": TASK_STATUS_OPEN,
                "priority": t.get("priority") or PRIORITY_DEFAULT,
                "due_at": t.get("due_at") or t.get("due_date"),
                "assigned_to_email": t.get("assigned_to_email") or default_assignee_email,
                "created_by_email": created_by_email or _service_email(),
                # Checklist metadata (migration 20260727000000). Only meaningful
                # for template-spawned tasks; _compact drops them when absent so
                # ad-hoc callers are unaffected.
                "is_required": t.get("is_required"),
                "sort_order": t.get("sort_order"),
                "template_key": t.get("template_key"),
            }),
        )
        created.append(row)
        existing.add(title)
    return created


def update_task(
    supa: "SupabaseClient",
    task_id: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Update an editable task field set (issue #195).

    Validates status and priority against the DB check constraints first so a bad
    value returns a message naming the valid ones instead of a wrapped 500.

    ``completed_at`` is derived, never passed in: moving to a closed status stamps
    it, moving back out clears it. A task showing "in_progress" with a completion
    timestamp is the kind of contradiction that makes a queue untrustworthy.
    """
    status = fields.get("status")
    if status is not None and status not in TASK_STATUSES:
        raise ValueError(f"unknown status '{status}'; must be one of {list(TASK_STATUSES)}")
    priority = fields.get("priority")
    if priority is not None and priority not in TASK_PRIORITIES:
        raise ValueError(f"unknown priority '{priority}'; must be one of {list(TASK_PRIORITIES)}")

    payload = dict(fields)
    if status is not None:
        payload["completed_at"] = (
            datetime.now(timezone.utc).isoformat() if status in TASK_STATUS_CLOSED else None
        )
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    return supa.update(TASKS_TABLE, task_id, payload)


def update_case(
    supa: "SupabaseClient",
    case_id: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Update an editable case field set.

    ``status`` is not editable here on purpose. Closing runs checks, writes a
    resolution and pushes a summary to the AMS — a bare status write would skip
    all three and leave a case that reads closed with nothing to show for it.
    Use the close endpoint.
    """
    if "status" in fields:
        raise ValueError("status is not editable here; close the case instead")
    priority = fields.get("priority")
    if priority is not None and priority not in TASK_PRIORITIES:
        raise ValueError(f"unknown priority '{priority}'; must be one of {list(TASK_PRIORITIES)}")

    payload = dict(fields)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    return supa.update(CASES_TABLE, case_id, payload)


def delete_task(supa: "SupabaseClient", task_id: str) -> None:
    """Hard-delete one task. Callers log it — see the API layer."""
    supa.delete(TASKS_TABLE, task_id)


# A case's children, in the order they must go: anything pointing at the case
# before the case itself. Not left to ON DELETE CASCADE — two of these tables
# belong to the shared agency_crm schema, so the constraint is not ours to
# assume, and a half-deleted case is worse than a refused one.
CASE_CHILD_TABLES = (DOCLINKS_TABLE, EVENTS_TABLE, DETAILS_TABLE, TASKS_TABLE)


def delete_case(supa: "SupabaseClient", case_id: str) -> dict[str, int]:
    """Delete a case and everything filed against it. Returns rows seen per child.

    Document links go, the documents themselves do not: they live in Nextcloud
    and deleting a case is not a reason to destroy the client's paperwork.
    """
    counts: dict[str, int] = {}
    for table in CASE_CHILD_TABLES:
        try:
            counts[table] = len(supa.select(
                table, columns="id", params={"case_id": f"eq.{case_id}"}, limit=1000))
        except Exception:  # noqa: BLE001 — a count is for the receipt, not the delete
            counts[table] = -1
        supa.delete_where(table, filters={"case_id": f"eq.{case_id}"})
    supa.delete(CASES_TABLE, case_id)
    return counts


def get_task(supa: "SupabaseClient", task_id: str) -> dict[str, Any] | None:
    try:
        rows = supa.select(TASKS_TABLE, columns="*", params={"id": f"eq.{task_id}"}, limit=1)
    except Exception:  # noqa: BLE001 — malformed uuid reads as not found
        return None
    return rows[0] if rows else None


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
