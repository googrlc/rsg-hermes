"""Cases — the service desk's work items, and the tasks under them.

Cases in ``agency_crm_cases`` with tasks in ``agency_crm_tasks``, documents in
``agency_crm_document_links``, plus the AMS push queue those writes drain
through (/api/queue/*, /api/casework/run).

Tasks live here rather than in their own router because the data model puts
them here: a task hangs off a case, and both delete paths share `_log_deletion`.
That answers the ownership question the split plan left open.

What is NOT settled: the *module* these routes call is `hermes/renewals/cases.py`
— generic case and task persistence filed under renewals, which its own
docstring admits ("alongside marketing/service/claims/etc."). The HTTP surface
is separable today; the module is not. Splitting renewals/cases.py into the
generic half (belongs with cases) and the renewal-identity half is the
prerequisite for extracting either app as a repo. See docs/repo-split-plan.md.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from hermes.routers import deps

log = logging.getLogger(__name__)

router = APIRouter()

# Filing category follows the case type, so a renewal's paperwork lands in the
# client's "Renewal Reviews" folder rather than a generic dump.
_CASE_TYPE_CATEGORY = {
    "renewal": "Renewal Reviews",
    "marketing": "Quotes",
    "service": "Correspondence",
}
_CASE_DOC_MAX_BYTES = 25 * 1024 * 1024


class CaseCreateRequest(BaseModel):
    title: str
    case_type: str = "service"          # renewal|service|claims|marketing|endorsement|...
    description: str | None = None
    priority: str = "medium"
    owner_email: str
    created_by_email: str | None = None
    insured_name: str | None = None
    insured_database_id: str | None = None   # NowCerts insured guid
    policy_number: str | None = None
    due_at: str | None = None


@router.post("/api/cases")
def create_case_endpoint(req: CaseCreateRequest):
    """Create a general agency_crm_cases row (any case_type) for any cockpit.
    Owner/creator emails are validated against agency_crm_users (FK guard)."""
    from hermes_core.due_dates import normalize_due
    from hermes.casework import store as C

    supa = deps.get_supa()
    creator = req.created_by_email or C._service_email()
    deps.require_users(supa, [("owner_email", req.owner_email), ("created_by_email", creator)])

    case_number = C.case_number(req.case_type)
    try:
        due_at = normalize_due(req.due_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        case = supa.insert("agency_crm_cases", C._compact({
            "case_type": req.case_type,
            "case_number": case_number,
            "title": req.title,
            "description": req.description,
            "status": "open",
            "priority": req.priority or "medium",
            "owner_email": req.owner_email,
            "created_by_email": creator,
            "insured_name": req.insured_name,
            "insured_database_id": req.insured_database_id,
            "policy_number": req.policy_number,
            "due_at": due_at,
        }))
        C.log_case_event(
            supa, case_id=str(case.get("id")), event_type="case_created",
            summary=f"{req.case_type} case opened: {req.title}", actor_email=creator,
        )
    except Exception as exc:
        log.exception("create case failed: %s", req.title)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "case": case}


@router.get("/api/case-templates")
def list_case_templates_endpoint():
    """The case-template menu (onboarding, off-boarding, endorsement, COI, ...).

    Static definitions, not a table — a checklist is code the agency reviews in a
    PR, not data somebody can quietly edit into meaninglessness.
    """
    from hermes.casework import templates as T

    return {"templates": T.list_templates()}


class CaseFromTemplateRequest(BaseModel):
    """Open a case from a template, with its whole checklist attached."""
    template_key: str
    owner_email: str
    insured_name: str | None = None
    insured_database_id: str | None = None
    policy_number: str | None = None
    created_by_email: str | None = None
    assigned_to_email: str | None = None   # default assignee for the checklist
    title: str | None = None               # override the template's title
    description: str | None = None
    priority: str | None = None
    due_at: str | None = None


@router.post("/api/cases/from-template")
def create_case_from_template_endpoint(req: CaseFromTemplateRequest):
    """Create a case AND its checklist in one call.

    This is the whole point of templates: an onboarding that exists as a case
    with no tasks is the same half-onboarded client we already had. If the tasks
    cannot be written the case is rolled back, so a caller never ends up with a
    bare case it believes is a full checklist.
    """
    from hermes.casework import templates as T
    from hermes_core.due_dates import due_in_days, normalize_due
    from hermes.casework import store as C

    tpl = T.get_template(req.template_key)
    if not tpl:
        raise HTTPException(
            status_code=404,
            detail=f"unknown template '{req.template_key}'; "
                   f"valid: {', '.join(sorted(T.CASE_TEMPLATES))}",
        )

    supa = deps.get_supa()
    creator = req.created_by_email or C._service_email()
    deps.require_users(supa, [
        ("owner_email", req.owner_email),
        ("created_by_email", creator),
        ("assigned_to_email", req.assigned_to_email),
    ])

    case_type = tpl["case_type"]
    case_number = C.case_number(case_type)
    # A template's due_days is a number of DAYS: counted from today's date, agency
    # time, and landing at close of business — not `utcnow() + n`, which carried
    # the creation second and, after 8pm ET, the wrong date outright.
    try:
        case_due = normalize_due(req.due_at) or (
            due_in_days(tpl["due_days"]) if tpl.get("due_days") else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        case = supa.insert("agency_crm_cases", C._compact({
            "case_type": case_type,
            "case_number": case_number,
            "template_key": req.template_key,
            "title": req.title or T.render_title(req.template_key, req.insured_name),
            "description": req.description or tpl.get("description"),
            "status": "open",
            "priority": req.priority or tpl.get("priority") or "medium",
            "owner_email": req.owner_email,
            "created_by_email": creator,
            "insured_name": req.insured_name,
            "insured_database_id": req.insured_database_id,
            "policy_number": req.policy_number,
            "due_at": case_due,
        }))
    except Exception as exc:
        log.exception("create case from template failed: %s", req.template_key)
        raise HTTPException(status_code=502, detail=str(exc))

    case_id = str(case.get("id"))
    try:
        created = C.create_tasks(
            supa,
            case_id=case_id,
            insured_database_id=req.insured_database_id,
            default_assignee_email=req.assigned_to_email or req.owner_email,
            created_by_email=creator,
            tasks=[{
                "title": t["title"],
                "description": t.get("description"),
                "priority": t.get("priority", "medium"),
                "is_required": bool(t.get("required")),
                "sort_order": i,
                "template_key": req.template_key,
                "due_at": due_in_days(t.get("due_days", 0)),
            } for i, t in enumerate(tpl["tasks"])],
        )
    except Exception as exc:
        # Roll the case back rather than leave a checklist-less shell behind.
        log.exception("template tasks failed for %s; rolling back case", case_number)
        try:
            supa.delete("agency_crm_cases", params={"id": f"eq.{case_id}"})
        except Exception:  # noqa: BLE001
            log.exception("rollback of case %s failed — orphaned case left behind", case_number)
        raise HTTPException(status_code=502, detail=f"checklist creation failed: {exc}")

    C.log_case_event(
        supa, case_id=case_id, event_type="case_created",
        summary=f"{tpl['label']} opened from template with {len(created)} tasks",
        actor_email=creator,
    )
    return {"ok": True, "case": case, "tasks": created, "task_count": len(created)}


@router.get("/api/cases/{case_id}/progress")
def case_progress_endpoint(case_id: str):
    """Checklist progress plus whether every required task is satisfied."""
    rows = deps.get_supa().select(
        "v_case_progress", columns="*", params={"case_id": f"eq.{case_id}"}, limit=1
    )
    if not rows:
        raise HTTPException(status_code=404, detail="case not found")
    return rows[0]


class CaseCloseRequest(BaseModel):
    """Close a case. ``resolution`` is what goes to the AMS — the checklist does not."""
    resolution: str
    resolved_by_email: str | None = None
    push_to_ams: bool = True


@router.post("/api/cases/{case_id}/close")
def close_case_endpoint(case_id: str, req: CaseCloseRequest):
    """Close a case, refusing while required tasks are open.

    The database enforces the same rule (trigger, migration 20260727000000) so
    closing straight through PostgREST cannot bypass it. This endpoint checks
    first anyway, to return a list of what is actually blocking rather than a
    raw constraint error.

    On close the resolution summary is pushed to the AMS; the per-task detail
    stays in the CRM, which is the system that needs it.
    """
    from hermes.casework import templates as T
    from hermes.casework import store as C

    supa = deps.get_supa()
    actor = req.resolved_by_email or C._service_email()
    deps.require_users(supa, [("resolved_by_email", req.resolved_by_email)])

    cases = supa.select("agency_crm_cases", columns="*", params={"id": f"eq.{case_id}"}, limit=1)
    if not cases:
        raise HTTPException(status_code=404, detail="case not found")
    case = cases[0]

    tasks = supa.select(
        "agency_crm_tasks", columns="*",
        params={"case_id": f"eq.{case_id}", "order": "sort_order.asc"}, limit=500,
    )
    blocking = [
        t for t in tasks
        if t.get("is_required") and t.get("status") not in ("completed", "cancelled")
    ]
    if blocking:
        raise HTTPException(status_code=409, detail={
            "error": "required tasks still open",
            "case_number": case.get("case_number"),
            "blocking": [{"id": t.get("id"), "title": t.get("title"),
                          "status": t.get("status")} for t in blocking],
            "hint": "complete them, or cancel the ones that did not apply to this case",
        })

    closed_at = datetime.utcnow().isoformat()
    try:
        # supa.update is (table, record_id, payload) — this used to pass the
        # payload as the id with a params= kwarg, so every close raised TypeError
        # and came back as a 502. Closing a case has never worked from the portal.
        updated = supa.update("agency_crm_cases", case_id, {
            "status": "closed",
            "closed_at": closed_at,
            "resolution": req.resolution,
            "resolved_by_email": actor,
        })
    except Exception as exc:
        log.exception("close case %s failed", case_id)
        raise HTTPException(status_code=502, detail=str(exc))

    if isinstance(updated, list):
        updated = updated[0] if updated else None
    case = updated or {**case, "status": "closed", "closed_at": closed_at,
                       "resolution": req.resolution}
    summary = T.build_summary(case, tasks)

    ams = {"pushed": False, "reason": "not requested"}
    if req.push_to_ams:
        try:
            from hermes.casework.executor import push_case_summary_to_ams

            ams = push_case_summary_to_ams(supa, case=case, summary=summary)
            if ams.get("pushed"):
                supa.update("agency_crm_cases", case_id,
                            {"ams_summary_sent_at": datetime.utcnow().isoformat()})
        except Exception as exc:  # noqa: BLE001
            # A closed case is closed. An AMS hiccup is a sync problem, not a
            # reason to refuse the close and make somebody redo the work.
            log.exception("AMS summary push failed for case %s", case_id)
            ams = {"pushed": False, "reason": str(exc)}

    C.log_case_event(
        supa, case_id=case_id, event_type="case_closed",
        summary=f"Closed: {req.resolution}", actor_email=actor,
    )
    return {"ok": True, "case": case, "summary": summary, "ams": ams}


@router.get("/api/cases")
def list_cases_endpoint(
    status: str | None = None,
    case_type: str | None = None,
    limit: int = 100,
    include_progress: bool = False,
):
    """List cases, newest first.

    ``include_progress`` merges each case's checklist state from v_case_progress —
    how far through it is, whether every required task is satisfied
    (``can_close``), and how many are still blocking. That is the question anyone
    actually asks about a case, and answering it here avoids a per-case round trip
    from callers that can only make one request (the MCP bridge, a morning brief).

    One extra query for the whole page, not one per case.
    """
    params: dict[str, str] = {"order": "created_at.desc"}
    if status:
        params["status"] = f"eq.{status}"
    if case_type:
        params["case_type"] = f"eq.{case_type}"
    supa = deps.get_supa()
    rows = supa.select("agency_crm_cases", columns="*", params=params, limit=limit)

    if include_progress and rows:
        prog_params: dict[str, str] = {}
        if status:
            prog_params["status"] = f"eq.{status}"
        prog = supa.select("v_case_progress", columns="*", params=prog_params, limit=max(limit, len(rows)))
        by_id = {str(p.get("case_id")): p for p in prog}
        for r in rows:
            p = by_id.get(str(r.get("id")))
            if not p:
                continue
            r["progress"] = {
                "tasks_total": p.get("tasks_total"),
                "tasks_done": p.get("tasks_done"),
                "required_total": p.get("required_total"),
                "required_done": p.get("required_done"),
                "required_blocking": p.get("required_blocking"),
                "can_close": p.get("can_close"),
            }
    return {"cases": rows, "count": len(rows)}


@router.get("/api/cases/blocked")
def list_blocked_cases_endpoint(limit: int = 100):
    """Open cases that cannot close yet, and the specific tasks blocking each.

    The morning-brief question: "what is stopping these from being finished?"
    Returns the blocking task titles, not just a count — a number tells you there
    is a problem, a title tells you what to do about it.
    """
    supa = deps.get_supa()
    prog = supa.select(
        "v_case_progress", columns="*",
        params={"status": "eq.open", "can_close": "is.false", "order": "opened_at.asc"},
        limit=limit,
    )
    out: list[dict[str, Any]] = []
    for p in prog:
        tasks = supa.select(
            "agency_crm_tasks", columns="id,title,status,due_at,assigned_to_email,is_required",
            params={"case_id": f"eq.{p.get('case_id')}", "is_required": "is.true",
                    "order": "sort_order.asc"},
            limit=100,
        )
        blocking = [t for t in tasks if t.get("status") not in ("completed", "cancelled")]
        out.append({
            "case_id": p.get("case_id"),
            "case_number": p.get("case_number"),
            "case_type": p.get("case_type"),
            "insured_name": p.get("insured_name"),
            "title": p.get("title"),
            "tasks_done": p.get("tasks_done"),
            "tasks_total": p.get("tasks_total"),
            "blocking": [{"title": t.get("title"), "assigned_to_email": t.get("assigned_to_email"),
                          "due_at": t.get("due_at")} for t in blocking],
        })
    return {"blocked_cases": out, "count": len(out)}


class TaskCreateRequest(BaseModel):
    """Create a task. As of issue #195 a task has three legitimate shapes:
    case-linked (case_id), client-but-no-case (insured_database_id), or purely
    internal (neither) — "update commission percentage" is not client work and
    should not have to borrow somebody's case to exist."""
    case_id: str | None = None
    insured_database_id: str | None = None
    title: str
    description: str | None = None
    priority: str = "medium"
    assigned_to_email: str | None = None
    created_by_email: str | None = None
    due_at: str | None = None


@router.post("/api/tasks")
def create_task_endpoint(req: TaskCreateRequest):
    """Create a task. assigned_to/created_by validated vs agency_crm_users.

    ``case_id`` is optional — omit it for internal work. Idempotent per title
    within the task's scope (its case, else its client, else the internal
    bucket), counting only OPEN tasks so a recurring chore isn't blocked forever
    by last month's completed copy.
    """
    from hermes_core.due_dates import normalize_due
    from hermes.casework import store as C

    supa = deps.get_supa()
    creator = req.created_by_email or C._service_email()
    deps.require_users(supa, [("assigned_to_email", req.assigned_to_email), ("created_by_email", creator)])

    try:
        due_at = normalize_due(req.due_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        created = C.create_tasks(
            supa, case_id=req.case_id,
            insured_database_id=req.insured_database_id,
            tasks=[{"title": req.title, "description": req.description,
                    "assigned_to_email": req.assigned_to_email,
                    "priority": req.priority, "due_at": due_at}],
            created_by_email=creator,
        )
    except Exception as exc:
        log.exception("create task failed: %s", req.title)
        raise HTTPException(status_code=502, detail=str(exc))
    if not created:
        # Title already open in this scope (idempotent no-op).
        return {"ok": True, "created": False, "task": None}
    # Best-effort: ping the team chat (Nextcloud Talk) about the new task. Never
    # let a chat hiccup fail the task create — it's fire-and-forget.
    try:
        from hermes.operations.task_notify import notify_task_created

        notify_task_created(created[0], kind="task")
    except Exception:  # noqa: BLE001
        log.exception("task_notify failed for %s", req.title)
    return {"ok": True, "created": True, "task": created[0]}


@router.post("/api/tasks/digest")
def post_task_digest():
    """Post the open-task digest to the team chat (Nextcloud Talk). Meant to be
    hit on a daily schedule (pg_cron / scheduler). No-op if NEXTCLOUD_TALK_TOKEN
    is unset."""
    from hermes.operations.task_notify import daily_task_digest

    return daily_task_digest(deps.get_supa())


@router.get("/api/tasks")
def list_tasks_endpoint(
    case_id: str | None = None,
    insured_id: str | None = None,
    scope: str | None = None,
    open_only: bool = False,
    limit: int = 200,
):
    """List tasks.

    ``scope='internal'`` returns only standalone tasks (no case) — the queue of
    things that are nobody's client work but still somebody's job. Without it the
    internal items are buried among case tasks, which is how they get missed.
    """
    params: dict[str, str] = {"order": "created_at.desc"}
    if case_id:
        params["case_id"] = f"eq.{case_id}"
    if insured_id:
        params["insured_database_id"] = f"eq.{insured_id}"
    if scope == "internal":
        params["case_id"] = "is.null"
    elif scope == "case":
        params["case_id"] = "not.is.null"
    if open_only:
        from hermes.casework.store import TASK_STATUS_CLOSED

        params["status"] = f"not.in.({','.join(TASK_STATUS_CLOSED)})"
    rows = deps.get_supa().select("agency_crm_tasks", columns="*", params=params, limit=limit)
    return {"tasks": rows, "count": len(rows)}


class TaskUpdateRequest(BaseModel):
    """Editable task fields. All optional — only what's provided is written."""
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to_email: str | None = None
    due_at: str | None = None
    case_id: str | None = None
    insured_database_id: str | None = None


@router.patch("/api/tasks/{task_id}")
def update_task_endpoint(task_id: str, req: TaskUpdateRequest):
    """Update a task (issue #195 — tasks were create-only and view-only).

    ``completed_at`` is derived from ``status``, never accepted from the caller.
    A reassignment is validated against agency_crm_users: assigned_to_email is a
    real FK, so an unknown address fails at the database with a message nobody
    can act on.
    """
    from hermes_core.due_dates import normalize_due
    from hermes.casework import store as C

    supa = deps.get_supa()
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields provided")
    if "due_at" in fields:
        try:
            fields["due_at"] = normalize_due(fields["due_at"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not C.get_task(supa, task_id):
        raise HTTPException(status_code=404, detail="task not found")
    if fields.get("assigned_to_email"):
        deps.require_users(supa, [("assigned_to_email", fields["assigned_to_email"])])
    try:
        row = C.update_task(supa, task_id, fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("update task failed: %s", task_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "task": row}


class DeleteRequest(BaseModel):
    deleted_by: str
    reason: str | None = None


def _log_deletion(supa, *, entity_type: str, entity_key: str, actor: str,
                  before: dict, reason: str | None) -> None:
    from hermes.overrides.store import write_log

    write_log(supa, entity_type=entity_type, entity_key=entity_key,
              action="deleted", actor=actor, before=before, note=reason)


@router.delete("/api/tasks/{task_id}")
def delete_task_endpoint(task_id: str, req: DeleteRequest):
    """Delete a task outright. Cancelling keeps it in the record; this removes it."""
    from hermes.casework import store as C

    supa = deps.get_supa()
    deps.require_users(supa, [("deleted_by", req.deleted_by)])
    task = C.get_task(supa, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        C.delete_task(supa, task_id)
    except Exception as exc:
        log.exception("delete task failed: %s", task_id)
        raise HTTPException(status_code=502, detail=str(exc))
    _log_deletion(supa, entity_type="agency_crm_tasks", entity_key=task_id,
                  actor=req.deleted_by, before=task, reason=req.reason)
    # A case-linked task leaves its trace on the case timeline too — the case is
    # where anyone will go looking for why the checklist got shorter.
    if task.get("case_id"):
        try:
            C.log_case_event(
                supa, case_id=str(task["case_id"]), event_type="task_deleted",
                summary=f"Task deleted: {task.get('title')}", actor_email=req.deleted_by,
            )
        except Exception:  # noqa: BLE001 — the task is already gone
            log.exception("case event for deleted task %s failed", task_id)
    return {"ok": True, "deleted": task_id, "task": task}


class CaseUpdateRequest(BaseModel):
    """Editable case fields. All optional — only what's provided is written.

    ``status`` is absent on purpose: closing a case runs the required-task
    checks, records a resolution and pushes a summary to the AMS. Use /close.
    Extras are rejected rather than dropped, so a caller who sends ``status``
    is told it is not editable instead of getting a silent no-op.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    priority: str | None = None
    owner_email: str | None = None
    due_at: str | None = None
    case_type: str | None = None
    insured_name: str | None = None
    policy_number: str | None = None


@router.patch("/api/cases/{case_id}")
def update_case_endpoint(case_id: str, req: CaseUpdateRequest):
    """Edit a case. Cases were create-and-close-only; a typo'd title or the wrong
    owner meant the case stayed wrong."""
    from hermes_core.due_dates import normalize_due
    from hermes.casework import store as C

    supa = deps.get_supa()
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields provided")
    if "due_at" in fields:
        try:
            fields["due_at"] = normalize_due(fields["due_at"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    rows = supa.select("agency_crm_cases", columns="*", params={"id": f"eq.{case_id}"}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="case not found")
    if fields.get("owner_email"):
        deps.require_users(supa, [("owner_email", fields["owner_email"])])
    try:
        row = C.update_case(supa, case_id, fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("update case failed: %s", case_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "case": row}


@router.delete("/api/cases/{case_id}")
def delete_case_endpoint(case_id: str, req: DeleteRequest):
    """Delete a case and everything filed against it — tasks, timeline, document
    links, renewal detail. The Nextcloud documents themselves are left alone."""
    from hermes.casework import store as C

    supa = deps.get_supa()
    deps.require_users(supa, [("deleted_by", req.deleted_by)])
    rows = supa.select("agency_crm_cases", columns="*", params={"id": f"eq.{case_id}"}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="case not found")
    case = rows[0]
    try:
        removed = C.delete_case(supa, case_id)
    except Exception as exc:
        log.exception("delete case failed: %s", case_id)
        raise HTTPException(status_code=502, detail=str(exc))
    _log_deletion(supa, entity_type="agency_crm_cases", entity_key=case_id,
                  actor=req.deleted_by, before=case, reason=req.reason)
    return {"ok": True, "deleted": case_id, "case": case, "removed": removed}


class PushToAmsRequest(BaseModel):
    approved_by: str


class QueueRetryRequest(BaseModel):
    requeued_by: str
    run_now: bool = True


@router.post("/api/cases/{case_id}/push-to-ams")
def push_case_to_ams(case_id: str, req: PushToAmsRequest):
    """Approved push: log this case in the NowCerts task ledger. approved_by must be a user."""
    from hermes.casework.executor import stage_case_job

    supa = deps.get_supa()
    deps.require_users(supa, [("approved_by", req.approved_by)])
    try:
        rows = supa.select("agency_crm_cases", columns="*", params={"id": f"eq.{case_id}"}, limit=1)
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        job = stage_case_job(supa, case=rows[0], approved_by=req.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("push case failed: %s", case_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "queued": True, "queue_id": job.get("id"),
            "note": "Case queued to NowCerts (approved). Writes when the casework executor runs."}


@router.post("/api/tasks/{task_id}/push-to-ams")
def push_task_to_ams(task_id: str, req: PushToAmsRequest):
    """Approved push: log this task in the NowCerts task ledger (uses its case's insured)."""
    from hermes.casework.executor import stage_task_job

    supa = deps.get_supa()
    deps.require_users(supa, [("approved_by", req.approved_by)])
    try:
        rows = supa.select("agency_crm_tasks", columns="*", params={"id": f"eq.{task_id}"}, limit=1)
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(status_code=404, detail="task not found")
    task = rows[0]
    insured_id, policy_number = None, None
    if task.get("case_id"):
        try:
            crows = supa.select("agency_crm_cases", columns="insured_database_id,policy_number",
                                params={"id": f"eq.{task['case_id']}"}, limit=1)
            if crows:
                insured_id = crows[0].get("insured_database_id")
                policy_number = crows[0].get("policy_number")
        except Exception:
            pass
    try:
        job = stage_task_job(supa, task=task, insured_database_id=insured_id,
                             policy_number=policy_number, approved_by=req.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("push task failed: %s", task_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "queued": True, "queue_id": job.get("id"),
            "note": "Task queued to NowCerts (approved). Writes when the casework executor runs."}


@router.get("/api/queue/failed")
def list_failed_ams_writebacks(limit: int = 100):
    """Service-request/client-task write-backs that failed or exhausted retries —
    the retry queue surfaced in the cockpit."""
    rows = deps.get_supa().select(
        "outbound_sync_queue", columns="*",
        params={"object_type": "in.(case,task)", "status": "in.(failed,dead)",
                "order": "updated_at.desc"}, limit=limit,
    )
    return {"jobs": rows, "count": len(rows)}


@router.post("/api/queue/{queue_id}/retry")
def retry_ams_writeback(queue_id: str, req: QueueRetryRequest):
    """Retriable on command: re-open a failed/dead case or task write-back and
    (by default) run the executor now so it relays to NowCerts immediately."""
    from hermes.casework.executor import requeue_job, run_casework_executor

    supa = deps.get_supa()
    deps.require_users(supa, [("requeued_by", req.requeued_by)])
    try:
        job = requeue_job(supa, queue_id=queue_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("requeue failed: %s", queue_id)
        raise HTTPException(status_code=502, detail=str(exc))
    run = run_casework_executor(supa=supa, limit=5) if req.run_now else {}
    return {"ok": True, "requeued": True, "queue_id": queue_id, "job": job, "run": run}


@router.post("/api/casework/run")
def run_casework_writebacks(req: deps.ExecutorRunRequest):
    """Run the case/task → NowCerts write-back executor on command (opt-in, no cron).
    ``dry_run`` previews without writing."""
    from hermes.casework.executor import run_casework_executor

    summary = run_casework_executor(supa=deps.get_supa(), limit=req.limit, dry_run=req.dry_run)
    return {"ok": True, **summary}


@router.post("/api/cases/{case_id}/documents")
async def upload_case_document(
    case_id: str,
    file: UploadFile = File(...),
    uploaded_by: str = Form(""),
    category: str = Form(""),
    title: str = Form(""),
):
    """Attach a file to a case: Nextcloud for the bytes, a doc-link row for the CRM.

    Same path the renewal PDF filer already uses (file_document -> link_document),
    so a hand-attached document lands in the same client folder tree as a generated
    one instead of a parallel store.
    """
    from hermes_integrations.nextcloud_client import CLIENT_CATEGORIES, NextcloudClient
    from hermes.casework import store as C

    supa = deps.get_supa()
    rows = []
    try:
        rows = supa.select("agency_crm_cases", columns="*", params={"id": f"eq.{case_id}"}, limit=1)
    except Exception:
        rows = []  # malformed uuid -> not found
    if not rows:
        raise HTTPException(status_code=404, detail="case not found")
    case = rows[0]

    uploader = uploaded_by.strip() or C._service_email()
    deps.require_users(supa, [("uploaded_by", uploader)])

    if category and category not in CLIENT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown category '{category}'; must be one of {list(CLIENT_CATEGORIES)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(content) > _CASE_DOC_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is {len(content) // 1024 // 1024}MB; the limit is "
                   f"{_CASE_DOC_MAX_BYTES // 1024 // 1024}MB",
        )

    filename = (file.filename or "attachment").strip()
    folder = category or _CASE_TYPE_CATEGORY.get(
        str(case.get("case_type") or ""), "Correspondence"
    )
    client_name = case.get("insured_name") or None

    try:
        filed = NextcloudClient().file_document(
            content=content,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            # No insured on the case -> Internal/Case Files rather than a client
            # folder. Guessing a client name would misfile it under someone.
            client=client_name,
            category=folder,
            internal_folder=None if client_name else "Case Files",
        )
    except Exception as exc:
        log.exception("case document upload failed: case=%s file=%s", case_id, filename)
        raise HTTPException(status_code=502, detail=f"Nextcloud upload failed: {exc}")

    try:
        link = C.link_document(
            supa,
            case_id=case_id,
            title=title.strip() or filename,
            nextcloud_path=filed["path"],
            nextcloud_url=filed.get("url"),
            insured_id=case.get("insured_database_id"),
            content_type=file.content_type,
            uploaded_by_email=uploader,
        )
    except Exception as exc:
        # The bytes are safely in Nextcloud; only the CRM link failed. Say so —
        # "upload failed" would send someone hunting for a file that is right there.
        log.exception("case document link failed: case=%s path=%s", case_id, filed["path"])
        raise HTTPException(
            status_code=502,
            detail=f"File stored at {filed['path']} but linking it to the case failed: {exc}",
        )

    # Keep the case's folder pointer current, as the renewal filer does.
    try:
        supa.update("agency_crm_cases", case_id,
                    {"nextcloud_folder_url": filed.get("url") or filed["path"]})
    except Exception:  # noqa: BLE001 — a pointer refresh must not fail the upload
        log.exception("case folder pointer update failed: %s", case_id)

    return {"ok": True, "document": link, "filed_to": filed["path"]}


@router.get("/api/cases/{case_id}/documents")
def case_documents_endpoint(case_id: str):
    """Nextcloud document links filed against a case (agency_crm_document_links)."""
    rows = deps.get_supa().select(
        "agency_crm_document_links", columns="*",
        params={"case_id": f"eq.{case_id}", "order": "created_at.desc"}, limit=200,
    )
    return {"documents": rows, "count": len(rows)}
