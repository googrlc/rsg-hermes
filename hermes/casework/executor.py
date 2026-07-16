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

from hermes.renewals.executor import (
    DESTINATION_NOWCERTS,
    QUEUE_COMPLETED,
    QUEUE_FAILED,
    QUEUE_PROCESSING,
    QUEUE_QUEUED,
    QUEUE_TABLE,
    _extract_created_id,
)

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.sync.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)

OBJECT_TYPE_CASE = "case"
OBJECT_TYPE_TASK = "task"
CASES_TABLE = "agency_crm_cases"
TASKS_TABLE = "agency_crm_tasks"

_PRIORITY = {"low": "Low", "medium": "Normal", "high": "High"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _priority(v: Any) -> str:
    return _PRIORITY.get(str(v or "medium").strip().lower(), "Normal")


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
    return supa.select(
        QUEUE_TABLE, columns="*",
        params={
            "object_type": f"in.({OBJECT_TYPE_CASE},{OBJECT_TYPE_TASK})",
            "destination_system": f"eq.{DESTINATION_NOWCERTS}",
            "status": f"eq.{QUEUE_QUEUED}",
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
        from hermes.integrations.supabase_client import SupabaseClient

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
            from hermes.sync.nowcerts_client import NowCertsClient

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
