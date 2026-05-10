"""OpenClaw async AI task queue helpers and worker."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from hermes.integrations.openclaw_producer import enqueue_openclaw_task as producer_enqueue_openclaw_task
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

DEFAULT_OPENCLAW_POLL_SECONDS = 5.0


def enqueue_openclaw_task(
    supa: SupabaseClient,
    *,
    task_type: str,
    payload: dict[str, Any],
    requested_by: str = "hermes",
    priority: int = 5,
    notify_slack: bool = False,
) -> dict[str, Any]:
    """Queue a task for OpenClaw (validated contract + retrying insert)."""
    return producer_enqueue_openclaw_task(
        supa,
        task_type=task_type,
        payload=payload,
        requested_by=requested_by,
        priority=priority,
        notify_slack=notify_slack,
    )


def process_openclaw_queue(
    supa: SupabaseClient,
    *,
    batch_size: int = 10,
) -> dict[str, Any]:
    """Process pending OpenClaw queue items and persist `result` payloads."""
    queued = supa.select(
        "openclaw_task_queue",
        params={
            "status": "eq.PENDING",
            "order": "priority.asc,created_at.asc",
        },
        limit=batch_size,
    )
    if not queued:
        return {"total": 0, "succeeded": 0, "failed": 0, "errors": []}

    succeeded = 0
    failed = 0
    errors: list[str] = []
    for task in queued:
        task_id = task["id"]
        attempt = (task.get("attempt_count") or 0) + 1
        try:
            supa.update(
                "openclaw_task_queue",
                task_id,
                {"status": "PROCESSING", "attempt_count": attempt},
            )
            result = _process_via_openclaw_skills(task)
            supa.update(
                "openclaw_task_queue",
                task_id,
                {"status": "SUCCESS", "result": result},
            )
            succeeded += 1
            if task.get("notify_slack"):
                _notify_slack_task_complete(task, result)
        except Exception as exc:
            failed += 1
            errors.append(f"{task_id}: {exc}")
            try:
                supa.update(
                    "openclaw_task_queue",
                    task_id,
                    {"status": "FAILED", "attempt_count": attempt, "result": {"error": str(exc)}},
                )
            except SupabaseClientError:
                log.exception("Failed to mark OpenClaw task failed task_id=%s", task_id)
    return {"total": len(queued), "succeeded": succeeded, "failed": failed, "errors": errors}


def run_openclaw_worker_loop(
    supa: SupabaseClient,
    *,
    poll_seconds: float = DEFAULT_OPENCLAW_POLL_SECONDS,
    batch_size: int = 25,
) -> None:
    """Continuously poll openclaw_task_queue every N seconds."""
    interval = poll_seconds if poll_seconds > 0 else DEFAULT_OPENCLAW_POLL_SECONDS
    log.info("Starting OpenClaw queue loop: interval=%ss batch_size=%s", interval, batch_size)
    while True:
        result = process_openclaw_queue(supa, batch_size=batch_size)
        if result["total"]:
            log.info(
                "OpenClaw queue processed: total=%s succeeded=%s failed=%s",
                result["total"],
                result["succeeded"],
                result["failed"],
            )
        time.sleep(interval)


def _process_via_openclaw_skills(task: dict[str, Any]) -> dict[str, Any]:
    """Placeholder OpenClaw skill execution contract."""
    return {
        "task_type": task.get("task_type"),
        "processed_by": "openclaw_skills",
        "payload": task.get("payload") or {},
        "status": "processed",
    }


def _notify_slack_task_complete(task: dict[str, Any], result: dict[str, Any]) -> None:
    if not os.environ.get("SLACK_BOT_TOKEN", "").strip():
        return
    try:
        SlackNotifier().post_message(
            text=(
                ":white_check_mark: OpenClaw enrichment complete\n"
                f"- task_id: {task.get('id')}\n"
                f"- task_type: {task.get('task_type')}\n"
                f"- requested_by: {task.get('requested_by')}\n"
                f"- result_status: {result.get('status', 'processed')}"
            )
        )
    except SlackNotifierError:
        log.exception("Failed to post OpenClaw completion Slack message task_id=%s", task.get("id"))
