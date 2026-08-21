"""Catalyst / Hermes service queue over Zoho CRM Service_Requests.

Path shapes match the deployed Catalyst bundle (`/api/desk`,
`/api/desk/cases/{id}`, `/api/desk/tasks/{id}`) so the UI can retarget
without a new table. Records are CRM Service_Requests.

This router does not call Zoho Desk, does not write NowCerts, and does
not read or write a Supabase service-desk store. Errors use
``{"error": "..."}`` because the live client (`api.js` ApiError) reads
``payload.error``, not FastAPI ``detail``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from hermes_app import deps
from hermes.zoho.service_requests import (
    MODULE_API_NAME,
    REQUEST_TYPES,
    lookup_id,
    catalyst_row,
    catalyst_vocab,
    desk_stage_to_status,
    matches_query,
    matches_view,
    normalize_request_type,
    record_get,
    status_to_desk_stage,
)

log = logging.getLogger(__name__)

router = APIRouter()

_LIST_FIELDS = ",".join(
    (
        "id",
        "Name",
        "Subject",
        "Description",
        "Request_Type",
        "Status",
        "Waiting_On",
        "Priority",
        "Policy_Number",
        "Client_Name",
        "Account_Name",
        "Owner",
        "Due_Date",
        "Service_Time",
        "Completion_Time",
        "Age_Days",
        "Overdue",
        "Closed_Date",
        "Next_Step",
        "Contact_Name",
        "Carrier",
        "Line_Of_Business",
        "Open_Date",
        "Last_Activity",
        "Modified_Time",
        "Request_Number",
        "Policy",
    )
)

_CLOSE_TASKS_COMPLETED = (
    "Send the client confirmation email from CRM",
    "File this request in AMS by hand (this desk does not write NowCerts)",
)
_CLOSE_TASKS_NOT_COMPLETED = (
    "Tell the client this request is not completed",
    "Update AMS only if something changed (this desk does not write NowCerts)",
)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _zoho():
    from hermes_integrations.zoho_client import ZohoClient, ZohoClientError, get_client

    try:
        return get_client()
    except ZohoClientError as exc:
        raise DeskError(503, f"Zoho CRM is not configured: {exc}") from exc


class DeskError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")


def _list_records(client, *, page: int = 1, per_page: int = 200) -> list[dict[str, Any]]:
    body = client._get(
        MODULE_API_NAME,
        params={
            "fields": _LIST_FIELDS,
            "page": page,
            "per_page": per_page,
            "sort_order": "desc",
            "sort_by": "Modified_Time",
        },
    )
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _get_record(client, record_id: str) -> dict[str, Any]:
    body = client._get(f"{MODULE_API_NAME}/{record_id}")
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    raise DeskError(404, f"{MODULE_API_NAME} {record_id} not found")


def _related_list(client, record_id: str, relation: str) -> list[dict[str, Any]]:
    try:
        body = client._get(f"{MODULE_API_NAME}/{record_id}/{relation}")
    except Exception:  # noqa: BLE001 — related lists are best-effort
        return []
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _get_module_record(client, module: str, record_id: str) -> dict[str, Any] | None:
    if not record_id:
        return None
    try:
        body = client._get(f"{module}/{record_id}")
    except Exception:  # noqa: BLE001
        return None
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def _card_payload(client, rec: dict[str, Any]) -> dict[str, Any]:
    row = catalyst_row(rec)
    record_id = str(rec.get("id") or row.get("id") or "")
    notes = _related_list(client, record_id, "Notes")
    tasks = _related_list(client, record_id, "Tasks")
    account_id = lookup_id(record_get(rec, "Account_Name"))
    policy_id = lookup_id(record_get(rec, "Policy"))
    contact_id = lookup_id(record_get(rec, "Contact_Name"))
    account = _get_module_record(client, "Accounts", account_id) if account_id else None
    policy = _get_module_record(client, "Policies", policy_id) if policy_id else None
    contact = _get_module_record(client, "Contacts", contact_id) if contact_id else None
    open_tasks = [
        t
        for t in tasks
        if str(t.get("Status") or "").lower() not in {"completed", "closed"}
    ]
    related: dict[str, Any] = {
        "account": {
            "id": account_id,
            "name": row.get("Account_Name") or row.get("Client_Name"),
            "NowCerts_Insured_GUID": (account or {}).get("NowCerts_Insured_GUID")
            if account
            else None,
        }
        if (account_id or row.get("Account_Name") or row.get("Client_Name"))
        else None,
        "policy": {
            "id": policy_id,
            "Policy_Number": (policy or {}).get("Policy_Number") or row.get("Policy_Number"),
            "Status": (policy or {}).get("Policy_Status") or (policy or {}).get("Status") or "",
            "Carrier": (policy or {}).get("Carrier") or row.get("Carrier") or "",
        }
        if (policy_id or row.get("Policy_Number"))
        else None,
        "contact": contact
        or (
            {"id": contact_id, "Full_Name": row.get("Contact_Name")}
            if (contact_id or row.get("Contact_Name"))
            else None
        ),
        "notes": notes,
    }
    return {
        "source": "zoho_crm",
        "module": MODULE_API_NAME,
        "not_desk": True,
        "case": row,
        "vocab": catalyst_vocab(),
        "related": related,
        "tasks": tasks,
        "steps_complete": not open_tasks,
    }


def _window_cutoff(window: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    key = (window or "month").strip().lower()
    if key in {"week", "7d"}:
        return now - timedelta(days=7)
    if key in {"month", "30", "30d"}:
        return now - timedelta(days=30)
    if key in {"quarter", "90d"}:
        return now - timedelta(days=90)
    if key in {"all", "*"}:
        return None
    return now - timedelta(days=30)


def _in_window(row: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    raw = row.get("Closed_Time") or row.get("Completed_At") or row.get("Open_Date") or ""
    if not raw:
        return True
    text = str(raw).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= cutoff


def _kpis(rows: list[dict[str, Any]], *, cutoff: datetime | None) -> dict[str, int]:
    open_n = waiting_n = overdue_n = done_n = 0
    for row in rows:
        desk = str(row.get("Desk_Stage") or "").lower()
        status = str(row.get("Status") or "").lower()
        if status == "waiting" or desk.startswith("waiting"):
            waiting_n += 1
        elif status == "completed" or desk == "done":
            if _in_window(row, cutoff):
                done_n += 1
        elif status in {"new", "in progress"} or desk in {"new", "in progress"}:
            open_n += 1
        if row.get("Overdue") and status != "completed" and desk != "done":
            overdue_n += 1
    return {
        "open": open_n,
        "waiting": waiting_n,
        "overdue": overdue_n,
        "done_month": done_n,
    }


def _require_write(request: Request) -> JSONResponse | None:
    try:
        deps.require_hermes_token(request)
    except HTTPException as exc:
        return _error(exc.status_code, str(exc.detail))
    return None


def _patch_crm_fields(body: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "Status",
        "Desk_Stage",
        "Waiting_On",
        "Next_Step",
        "Request_Type",
        "Priority",
        "Due_Date",
        "Description",
        "Subject",
        "Policy_Number",
    }
    crm: dict[str, Any] = {}
    for key, value in (body or {}).items():
        if key not in allowed:
            continue
        if key == "Desk_Stage":
            status, waiting_on = desk_stage_to_status(str(value))
            crm["Status"] = status
            if waiting_on:
                crm["Waiting_On"] = waiting_on
            elif status != "Waiting":
                crm["Waiting_On"] = None
        elif key == "Request_Type":
            crm["Request_Type"] = normalize_request_type(str(value)) or value
        elif key == "Status":
            crm["Status"] = desk_stage_to_status(str(value))[0] if str(value).lower() in {
                "in progress",
                "done",
                "waiting on carrier",
                "waiting on client",
            } else value
        else:
            crm[key] = value
    return crm


def _create_close_tasks(client, record_id: str, *, completed: bool) -> list[dict[str, Any]]:
    subjects = _CLOSE_TASKS_COMPLETED if completed else _CLOSE_TASKS_NOT_COMPLETED
    created: list[dict[str, Any]] = []
    for subject in subjects:
        payload = {
            "Subject": subject,
            "What_Id": record_id,
            "$se_module": MODULE_API_NAME,
            "Status": "Not Started",
        }
        try:
            body = client._post("Tasks", {"data": [payload]})
            rid = None
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, list) and data and isinstance(data[0], dict):
                details = data[0].get("details") or data[0]
                rid = details.get("id")
            created.append({"id": rid, "Subject": subject})
        except Exception as exc:  # noqa: BLE001 — close still succeeds
            log.exception("CRM Task create failed for Service_Requests close-out")
            created.append({"Subject": subject, "error": str(exc)[:200]})
    return created


@router.get("/api/desk")
def list_desk_queue(
    view: str = Query("worklist"),
    stage: str = Query(""),
    type: str = Query("", alias="type"),
    q: str = Query(""),
    mine: str = Query("1"),
    window: str = Query("month"),
    limit: int = Query(200, ge=1, le=500),
):
    """Work queue over CRM Service_Requests (Catalyst-compatible)."""
    try:
        client = _zoho()
        records = _list_records(client, per_page=min(limit, 200))
    except DeskError as exc:
        return _error(exc.status, exc.message)
    except Exception as exc:  # noqa: BLE001
        log.exception("Service_Requests list failed")
        return _error(502, f"Zoho CRM read failed: {exc}")

    rows = [catalyst_row(rec) for rec in records]
    cutoff = _window_cutoff(window)
    scoped: list[dict[str, Any]] = []
    for row in rows:
        if not matches_query(row, q=q, type_filter=type):
            continue
        if mine in {"1", "true", "yes"}:
            # No CSR identity on this private API — do not invent an Owner filter.
            pass
        scoped.append(row)

    view_rows = [row for row in scoped if matches_view(row, view=view, stage=stage)]
    if (view or "").strip().lower() == "completed":
        view_rows = [row for row in view_rows if _in_window(row, cutoff)]
    shown_rows = view_rows[:limit]
    labels = {t: t for t in REQUEST_TYPES}
    empty = None if shown_rows else "No Service_Requests match this view."
    return {
        "source": "zoho_crm",
        "module": MODULE_API_NAME,
        "not_desk": True,
        "view": view,
        "mine": mine,
        "rows": shown_rows,
        "shown": len(shown_rows),
        "total": len(view_rows),
        "count": len(shown_rows),
        "kpis": _kpis(scoped, cutoff=cutoff),
        "request_types": list(REQUEST_TYPES),
        "request_type_labels": labels,
        "empty_reason": empty,
    }


@router.get("/api/desk/cases/{record_id}")
def get_desk_case(record_id: str):
    try:
        client = _zoho()
        rec = _get_record(client, record_id)
        return _card_payload(client, rec)
    except DeskError as exc:
        return _error(exc.status, exc.message)
    except Exception as exc:  # noqa: BLE001
        return _error(502, f"Zoho CRM read failed: {exc}")


@router.patch("/api/desk/cases/{record_id}")
def patch_desk_case(
    record_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
):
    """Save Desk Information onto Service_Requests. CRM only — not Desk, not NowCerts."""
    denied = _require_write(request)
    if denied is not None:
        return denied
    crm = _patch_crm_fields(body or {})
    if not crm:
        return _error(400, "no updatable Service_Requests fields")
    crm["Last_Activity"] = _now_iso()
    if str(crm.get("Status") or "").lower() == "completed" and "Closed_Date" not in crm:
        crm["Closed_Date"] = crm["Last_Activity"]
    try:
        client = _zoho()
        client._put(MODULE_API_NAME, {"data": [{"id": record_id, **crm}]})
        rec = _get_record(client, record_id)
        return _card_payload(client, rec)
    except DeskError as exc:
        return _error(exc.status, exc.message)
    except Exception as exc:  # noqa: BLE001
        return _error(502, f"Zoho CRM write failed: {exc}")


@router.post("/api/desk/cases/{record_id}/close")
def close_desk_case(
    record_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
):
    """Close-out: Status=Completed on the SR + CRM Tasks. Never NowCerts, never Desk."""
    denied = _require_write(request)
    if denied is not None:
        return denied
    disposition = str((body or {}).get("disposition") or "completed").strip().lower()
    completed = disposition in {"completed", "done", "complete"}
    now = _now_iso()
    crm = {
        "Status": "Completed",
        "Waiting_On": None,
        "Closed_Date": now,
        "Last_Activity": now,
        "Next_Step": (body or {}).get("next_step") or disposition,
    }
    try:
        client = _zoho()
        client._put(MODULE_API_NAME, {"data": [{"id": record_id, **crm}]})
        tasks = _create_close_tasks(client, record_id, completed=completed)
        rec = _get_record(client, record_id)
        card = _card_payload(client, rec)
        if completed:
            note = "Closed. Send the confirmation email from CRM and file AMS by hand."
            manual = [
                "Send the client confirmation email from CRM (Tasks).",
                "File the request in NowCerts / AMS by hand. This desk does not write NowCerts.",
            ]
        else:
            note = "Closed as not completed. Tell the client, and update AMS only if something changed."
            manual = [
                "Tell the client this request is not completed.",
                "Update AMS by hand only if something changed. This desk does not write NowCerts.",
            ]
        return {
            **card,
            "completed": completed,
            "ams": {"note": note, "manual_steps": manual},
            "tasks": tasks,
        }
    except DeskError as exc:
        return _error(exc.status, exc.message)
    except Exception as exc:  # noqa: BLE001
        return _error(502, f"Zoho CRM write failed: {exc}")


@router.post("/api/desk/cases/{record_id}/email")
def email_desk_case(
    record_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
):
    """CRM send-mail on the Service_Requests record. Not Desk, not NowCerts."""
    denied = _require_write(request)
    if denied is not None:
        return denied
    payload = dict(body or {})
    to_email = (payload.get("to_email") or "").strip()
    if not to_email:
        return _error(400, "to_email is required")
    mail = {
        "data": [
            {
                "from": {
                    "email": payload.get("from_email") or "",
                    "user_name": payload.get("from_name") or "Risk Solutions Group",
                },
                "to": [
                    {
                        "email": to_email,
                        "user_name": payload.get("to_name") or "",
                    }
                ],
                "subject": payload.get("subject") or "",
                "content": payload.get("content") or "",
                "mail_format": "html",
            }
        ]
    }
    try:
        client = _zoho()
        _get_record(client, record_id)
        client._post(f"{MODULE_API_NAME}/{record_id}/actions/send_mail", mail)
        rec = _get_record(client, record_id)
        row = catalyst_row(rec)
        return {
            "ok": True,
            "module": MODULE_API_NAME,
            "to": to_email,
            "desk_stage": row.get("Desk_Stage") or status_to_desk_stage(row.get("Status")),
        }
    except DeskError as exc:
        return _error(exc.status, exc.message)
    except Exception as exc:  # noqa: BLE001
        return _error(502, f"Zoho CRM send-mail failed: {exc}")


@router.patch("/api/desk/tasks/{task_id}")
def patch_desk_task(
    task_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
):
    """Complete a CRM Task. Not NowCerts, not Desk."""
    denied = _require_write(request)
    if denied is not None:
        return denied
    allowed = {"Status", "Subject", "Due_Date", "Description"}
    crm = {k: v for k, v in (body or {}).items() if k in allowed}
    if not crm:
        return _error(400, "no updatable Tasks fields")
    try:
        client = _zoho()
        client._put("Tasks", {"data": [{"id": task_id, **crm}]})
        return {"ok": True, "id": task_id, "module": "Tasks", "not_desk": True}
    except DeskError as exc:
        return _error(exc.status, exc.message)
    except Exception as exc:  # noqa: BLE001
        return _error(502, f"Zoho CRM Task write failed: {exc}")
