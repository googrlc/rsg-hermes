"""Case/task write-back executor — approved case or task → NowCerts task (opt-in).

Cockpit cases/tasks live in Supabase (``agency_crm_cases`` / ``agency_crm_tasks``).
Pushing one to the AMS logs it in the NowCerts task ledger via ``insert_task``
(``/api/Zapier/InsertTask``) — the same call the renewal executor uses. The cockpit
enqueues an approval-gated ``outbound_sync_queue`` row (``object_type='case'`` or
``'task'``); this executor claims it, writes the NowCerts task, and stamps
``nowcerts_task_id`` (+ ``sync_status='synced'`` for tasks) back on the row.

Same guarantees as intake/quote executors: nothing writes synchronously, guarded
claim, dry-run previews, opt-in (no auto-cron until validated live).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes_core.queue import (
    DESTINATION_NOWCERTS,
    OBJECT_TYPE_CASE,
    OBJECT_TYPE_TASK,
    QUEUE_COMPLETED,
    QUEUE_FAILED,
    QUEUE_PROCESSING,
    QUEUE_QUEUED,
    QUEUE_TABLE,
    extract_created_id as _extract_created_id,
    utcnow as _utcnow,
)

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient
    from hermes_integrations.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)

CASES_TABLE = "agency_crm_cases"
TASKS_TABLE = "agency_crm_tasks"

_PRIORITY = {"low": "Low", "medium": "Normal", "high": "High"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _priority(v: Any) -> str:
    return _PRIORITY.get(str(v or "medium").strip().lower(), "Normal")


class MissingDueDate(ValueError):
    """A task cannot reach the AMS without a due date.

    NowCerts requires ``due_date`` on InsertTask, and so does the AMS MCP's
    insert_task_tool. We were never sending one — consistent with the live data:
    18 tasks, not one carrying a nowcerts_task_id, and no failed queue rows
    either. The push has never worked.

    Raised at STAGE time, not in the executor, so the gap reaches whoever is
    pushing the task while they can still fix it, rather than becoming a failed
    queue row found later. Deliberately not defaulted: a made-up due date on a
    real client task is a date somebody works to.
    """


def _due_date(row: dict[str, Any], *, what: str) -> str:
    """The AMS-bound due date for a case/task row. Required, never invented."""
    from hermes_core.due_dates import normalize_due

    raw = row.get("due_at") or row.get("due_date")
    due = normalize_due(raw) if raw else None
    if not due:
        raise MissingDueDate(
            f"{what} has no due date, and NowCerts requires one on every task. "
            "Set a due date on it and push again."
        )
    return due


def nowcerts_agent_id(supa, email: str | None) -> str | None:
    """The NowCerts agent UUID for a CRM user, or None.

    Read from agency_crm_users.nowcerts_agent_id — stored once, exactly. There is
    deliberately no name-matching fallback: get_agent_id_by_name_tool would
    resolve a person by display name, and a near-miss there assigns a client's
    task to the wrong agent silently. An unassigned task is visible and gets
    picked up; a misassigned one looks done to everybody except the client.

    None means push the task unassigned, which insert_task_tool allows.
    """
    if not email:
        return None
    try:
        rows = supa.select(
            "agency_crm_users", columns="email,nowcerts_agent_id",
            params={"email": f"eq.{email}"}, limit=1,
        )
    except Exception:  # noqa: BLE001 — an unresolvable assignee must not fail the push
        log.exception("could not read the NowCerts agent id for %s", email)
        return None
    return (rows[0].get("nowcerts_agent_id") if rows else None) or None


def with_assignee(payload: dict[str, Any], agent_id: str | None) -> dict[str, Any]:
    """Attach assigned_to only when the agent is known exactly."""
    if agent_id:
        payload = {**payload, "assigned_to": [agent_id]}
    return payload


def map_case_to_task(case: dict[str, Any]) -> dict[str, Any]:
    """NowCerts InsertTask body (snake_case) from an agency_crm_cases row."""
    return {
        "title": case.get("title") or f"Case {case.get('case_number', '')}".strip(),
        "status": "Open",
        "priority": _priority(case.get("priority")),
        "description": case.get("description") or "",
        "insured_database_id": case.get("insured_database_id"),
        "policy_number": case.get("policy_number"),
        "category_name": str(case.get("case_type") or "Service").title(),
        "due_date": _due_date(case, what=f"case {case.get('case_number') or case.get('id')}"),
    }


def map_task_to_task(task: dict[str, Any], *, insured_database_id: str, policy_number: str | None = None) -> dict[str, Any]:
    """NowCerts InsertTask body from an agency_crm_tasks row (+ its case's insured)."""
    return {
        "title": task.get("title") or "Task",
        "status": "Completed" if str(task.get("status")) == "completed" else "Open",
        "priority": _priority(task.get("priority")),
        "description": task.get("description") or "",
        "insured_database_id": insured_database_id,
        "policy_number": policy_number,
        "category_name": "Task",
        "due_date": _due_date(task, what=f"task {task.get('title') or task.get('id')}"),
    }


def _stage(supa, *, object_type, target_table, target_id, task_payload, approved_by):
    return supa.insert(
        QUEUE_TABLE,
        {
            "object_type": object_type,
            "object_id": target_id,
            "destination_system": DESTINATION_NOWCERTS,
            "action": "create",
            "payload": {
                "action": "insert_task",
                "target_table": target_table,
                "target_id": target_id,
                "task": task_payload,
            },
            "status": QUEUE_QUEUED,
            "attempt_count": 0,
            "approved_by": approved_by,
            "approved_at": _utcnow().isoformat(),
        },
    )


def stage_case_job(supa: "SupabaseClient", *, case: dict[str, Any], approved_by: str) -> dict[str, Any]:
    if not case.get("id"):
        raise ValueError("case id is required")
    if not case.get("insured_database_id"):
        raise ValueError("case has no insured_database_id — link an insured before pushing to NowCerts")
    return _stage(supa, object_type=OBJECT_TYPE_CASE, target_table=CASES_TABLE,
                  target_id=str(case["id"]), task_payload=map_case_to_task(case), approved_by=approved_by)


def map_case_close_to_task(case: dict[str, Any], summary: str) -> dict[str, Any]:
    """NowCerts body for a CLOSED case.

    The AMS gets the outcome, not the checklist: one closed task carrying the
    resolution summary. The per-task detail and timings stay in the CRM, which is
    the system that needs them for reporting and for training the data.
    """
    body = map_case_to_task(case)
    body["status"] = "Closed"
    body["description"] = summary
    return body


def push_case_summary_to_ams(supa: "SupabaseClient", *, case: dict[str, Any],
                             summary: str) -> dict[str, Any]:
    """Stage the closing summary for NowCerts.

    Staged, not written directly — the AMS write path is the approved
    outbound_sync_queue drained by the executor, with retry and dead-lettering.
    A case with no linked insured cannot be pushed; that is reported rather than
    raised, because failing to sync must never block closing a finished case.
    """
    if not case.get("insured_database_id"):
        return {"pushed": False, "reason": "case has no insured_database_id — nothing to attach in NowCerts"}
    job = _stage(
        supa,
        object_type=OBJECT_TYPE_CASE,
        target_table=CASES_TABLE,
        target_id=str(case["id"]),
        task_payload=map_case_close_to_task(case, summary),
        approved_by=case.get("resolved_by_email") or "system",
    )
    return {"pushed": True, "queued": True, "queue_id": job.get("id"), "job": job}


def stage_task_job(supa: "SupabaseClient", *, task: dict[str, Any], insured_database_id: str,
                   approved_by: str, policy_number: str | None = None) -> dict[str, Any]:
    if not task.get("id"):
        raise ValueError("task id is required")
    if not insured_database_id:
        raise ValueError("the task's case has no insured_database_id — cannot push to NowCerts")
    payload = map_task_to_task(task, insured_database_id=insured_database_id, policy_number=policy_number)
    return _stage(supa, object_type=OBJECT_TYPE_TASK, target_table=TASKS_TABLE,
                  target_id=str(task["id"]), task_payload=payload, approved_by=approved_by)


def _eligible_jobs(supa, limit):
    # Local import: retry.py imports OBJECT_TYPE_CASE/TASK from here.
    from hermes_core.queue import due_filter

    return supa.select(
        QUEUE_TABLE, columns="*",
        params={
            "object_type": f"in.({OBJECT_TYPE_CASE},{OBJECT_TYPE_TASK})",
            "destination_system": f"eq.{DESTINATION_NOWCERTS}",
            "status": f"eq.{QUEUE_QUEUED}",
            **due_filter(),
            "order": "created_at.asc",
        },
        limit=limit,
    )


def run_casework_executor(
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
    limit: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process up to ``limit`` approved case/task jobs. ``dry_run`` is side-effect-free."""
    if supa is None:
        from hermes_integrations.supabase_client import SupabaseClient

        supa = SupabaseClient()
    summary: dict[str, Any] = {"claimed": 0, "completed": 0, "failed": 0, "previews": []}

    for job in _eligible_jobs(supa, limit)[:limit]:
        payload = dict(job.get("payload") or {})
        task_payload = payload.get("task") or {}
        target_table = payload.get("target_table")
        target_id = payload.get("target_id")

        if dry_run:
            summary["previews"].append({"queue_id": job.get("id"), "object_type": job.get("object_type"),
                                        "target": f"{target_table}/{target_id}", "task": task_payload})
            continue

        claimed = supa.update_where(
            QUEUE_TABLE,
            {"status": QUEUE_PROCESSING, "updated_at": _utcnow().isoformat()},
            filters={"id": f"eq.{job.get('id')}", "status": f"eq.{QUEUE_QUEUED}"},
        )
        if not claimed:
            continue
        summary["claimed"] += 1

        if nowcerts is None:
            from hermes_integrations.nowcerts_client import NowCertsClient

            nowcerts = NowCertsClient()

        try:
            resp = nowcerts.insert_task(task_payload)
            task_id = _extract_created_id(resp)
            if not task_id:
                raise RuntimeError("NowCerts returned no id for the created task")
            stamp: dict[str, Any] = {"nowcerts_task_id": task_id}
            if target_table == TASKS_TABLE:
                stamp["sync_status"] = "synced"
            if target_table and target_id:
                try:
                    supa.update(target_table, target_id, stamp)
                except Exception:
                    log.exception("casework: failed to stamp %s/%s", target_table, target_id)
            supa.update(QUEUE_TABLE, job.get("id"),
                        {"status": QUEUE_COMPLETED, "updated_at": _utcnow().isoformat()})
            summary["completed"] += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("casework executor failed on queue_id=%s", job.get("id"))
            supa.update(QUEUE_TABLE, job.get("id"),
                        {"status": QUEUE_FAILED, "updated_at": _utcnow().isoformat(),
                         "last_error": str(exc)[:2000]})
            summary["failed"] += 1

    return summary


# Statuses a case/task job can be retried from. 'dead' = retries exhausted by an
# automated backoff; both are re-openable on command.
RETRYABLE_STATUSES = (QUEUE_FAILED, "dead")


def requeue_job(supa: "SupabaseClient", *, queue_id: str) -> dict[str, Any]:
    """Re-open a failed/dead case or task write-back so the executor retries it.

    This is the "retriable on command" path: it flips the row back to ``queued``,
    bumps ``attempt_count``, clears the backoff (``scheduled_for``) and the stale
    ``last_error``. Guarded to case/task jobs in a retryable status only — never
    touches a completed or in-flight row.
    """
    rows = supa.select(QUEUE_TABLE, columns="*", params={"id": f"eq.{queue_id}"}, limit=1)
    if not rows:
        raise ValueError(f"queue row {queue_id} not found")
    job = rows[0]
    if job.get("object_type") not in (OBJECT_TYPE_CASE, OBJECT_TYPE_TASK):
        raise ValueError("only service-request (case) and client-task jobs are retriable here")
    if job.get("status") not in RETRYABLE_STATUSES:
        raise ValueError(
            f"job status is {job.get('status')!r}; only {'/'.join(RETRYABLE_STATUSES)} jobs can be retried"
        )
    return supa.update(
        QUEUE_TABLE,
        queue_id,
        {
            "status": QUEUE_QUEUED,
            "attempt_count": int(job.get("attempt_count") or 0) + 1,
            "scheduled_for": None,
            "last_error": None,
            "updated_at": _utcnow().isoformat(),
        },
    )
