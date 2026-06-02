"""Team Queue (Command Center, Phase 4).

Gretchen's / Lamar's open EspoCRM Tasks in plain English, with the one allowed
write-back: mark a task done. No IDs or jargon in the rendered output — just the
task, who owns it, what it's attached to, and when it's due.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

# Statuses that mean "still on someone's plate".
CLOSED_STATUSES = ["Completed", "Canceled"]
_TASK_SELECT = "id,name,status,dateEnd,priority,assignedUserName,parentName,parentType"


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _due_phrase(date_end: Any, today: date) -> tuple[str, int | None]:
    d = _parse_date(date_end)
    if d is None:
        return ("no due date", None)
    days = (d - today).days
    if days < 0:
        return (f"overdue {abs(days)}d", days)
    if days == 0:
        return ("due today", 0)
    if days == 1:
        return ("due tomorrow", 1)
    return (f"due in {days}d", days)


def shape_task(row: dict[str, Any], today: date) -> dict[str, Any]:
    due_label, due_days = _due_phrase(row.get("dateEnd"), today)
    return {
        "id": row.get("id"),
        "title": row.get("name") or "(untitled task)",
        "assignee": row.get("assignedUserName") or "Unassigned",
        "related": row.get("parentName"),
        "priority": row.get("priority") or "Normal",
        "status": row.get("status"),
        "due_label": due_label,
        "due_days": due_days,
        "date_end": row.get("dateEnd"),
    }


def list_open_tasks(client, *, today: date | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Open tasks (not Completed/Canceled), most urgent first."""
    today = today or date.today()
    body = client.get(
        "Task",
        params={
            "maxSize": limit,
            "select": _TASK_SELECT,
            "where": [{"type": "notIn", "attribute": "status", "value": CLOSED_STATUSES}],
            "orderBy": "dateEnd",
            "order": "asc",
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    tasks = [shape_task(r, today) for r in rows if isinstance(r, dict)]
    # tasks with no due date sort last; otherwise by urgency
    tasks.sort(key=lambda t: (t["due_days"] is None, t["due_days"] if t["due_days"] is not None else 0))
    return tasks


def group_by_assignee(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        grouped.setdefault(t["assignee"], []).append(t)
    return grouped


def complete_task(client, task_id: str) -> dict[str, Any]:
    """Mark a Task Completed in EspoCRM (the one allowed write-back)."""
    if not task_id:
        raise ValueError("task_id is required.")
    result = client.update("Task", task_id, {"status": "Completed"})
    return result if isinstance(result, dict) else {"id": task_id, "status": "Completed"}
