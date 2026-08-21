"""Catalyst / Hermes service queue over Zoho CRM Service_Requests.

Path shapes match the deployed Catalyst bundle (`/api/desk`,
`/api/desk/cases/{id}`) so the UI can retarget without a new table.
Records are CRM Service_Requests. This router does not call Zoho Desk
and does not read or write a Supabase service-desk store.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from hermes_app import deps
from hermes.zoho.service_requests import (
    MODULE_API_NAME,
    REQUEST_TYPES,
    catalyst_row,
    matches_query,
    matches_view,
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
    )
)


def _zoho():
    from hermes_integrations.zoho_client import ZohoClient, ZohoClientError, get_client

    try:
        return get_client()
    except ZohoClientError as exc:
        raise HTTPException(status_code=503, detail=f"Zoho CRM is not configured: {exc}") from exc


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
    raise HTTPException(status_code=404, detail=f"{MODULE_API_NAME} {record_id} not found")


def _window_cutoff(window: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    key = (window or "month").strip().lower()
    if key in {"week", "7d"}:
        return now - timedelta(days=7)
    if key in {"month", "30d"}:
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


@router.get("/api/desk")
def list_desk_queue(
    view: str = Query("desk"),
    stage: str = Query(""),
    type: str = Query("", alias="type"),
    q: str = Query(""),
    mine: str = Query(""),
    window: str = Query("month"),
    limit: int = Query(200, ge=1, le=500),
):
    """Work queue over CRM Service_Requests (Catalyst-compatible)."""
    try:
        client = _zoho()
        records = _list_records(client, per_page=min(limit, 200))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("Service_Requests list failed")
        raise HTTPException(status_code=502, detail=f"Zoho CRM read failed: {exc}") from exc

    rows = [catalyst_row(rec) for rec in records]
    cutoff = _window_cutoff(window) if view.lower() == "completed" else None
    filtered = []
    for row in rows:
        if not matches_view(row, view=view, stage=stage):
            continue
        if not matches_query(row, q=q, type_filter=type):
            continue
        if cutoff is not None and not _in_window(row, cutoff):
            continue
        if mine in {"1", "true", "yes"}:
            # No current-user context on this private API — ignore rather than
            # invent a filter against a fake user.
            pass
        filtered.append(row)
        if len(filtered) >= limit:
            break
    labels = {t: t for t in REQUEST_TYPES}
    return {
        "source": "zoho_crm",
        "module": MODULE_API_NAME,
        "not_desk": True,
        "view": view,
        "rows": filtered,
        "count": len(filtered),
        "request_types": list(REQUEST_TYPES),
        "request_type_labels": labels,
        "empty_reason": None if filtered else "No Service_Requests match this view.",
    }


@router.get("/api/desk/cases/{record_id}")
def get_desk_case(record_id: str):
    try:
        client = _zoho()
        rec = _get_record(client, record_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Zoho CRM read failed: {exc}") from exc
    row = catalyst_row(rec)
    return {
        "source": "zoho_crm",
        "module": MODULE_API_NAME,
        "not_desk": True,
        "case": row,
        "record": rec,
    }


@router.patch("/api/desk/cases/{record_id}")
def patch_desk_case(
    record_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
):
    """Update Status / Next_Step / Request_Type on Service_Requests. CRM only."""
    deps.require_hermes_token(request)
    payload = dict(body or {})
    allowed = {
        "Status",
        "Desk_Stage",
        "Next_Step",
        "Request_Type",
        "Priority",
        "Due_Date",
        "Description",
        "Subject",
    }
    crm: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in allowed:
            continue
        if key == "Desk_Stage":
            crm["Status"] = _desk_stage_to_status(str(value))
        else:
            crm[key] = value
    if not crm:
        raise HTTPException(status_code=400, detail="no updatable Service_Requests fields")
    crm["Last_Activity"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    if str(crm.get("Status") or "").lower() == "completed" and "Closed_Date" not in crm:
        crm["Closed_Date"] = crm["Last_Activity"]
    try:
        client = _zoho()
        client._put(MODULE_API_NAME, {"data": [{"id": record_id, **crm}]})
        rec = _get_record(client, record_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Zoho CRM write failed: {exc}") from exc
    return {"ok": True, "module": MODULE_API_NAME, "case": catalyst_row(rec)}


@router.post("/api/desk/cases/{record_id}/close")
def close_desk_case(
    record_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
):
    deps.require_hermes_token(request)
    return patch_desk_case(
        record_id,
        request,
        {"Status": "Completed", "Next_Step": (body or {}).get("disposition") or ""},
    )


@router.post("/api/desk/cases/{record_id}/email")
def email_desk_case(record_id: str):
    raise HTTPException(
        status_code=501,
        detail=(
            "Send mail from Zoho CRM on the Service_Requests record "
            f"({record_id}). Hermes does not open Zoho Desk."
        ),
    )


def _desk_stage_to_status(stage: str) -> str:
    raw = (stage or "").strip()
    if raw.lower() == "in progress":
        return "In Progress"
    return raw
