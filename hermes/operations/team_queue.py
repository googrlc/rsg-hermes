"""Team Queue (Command Center, Phase 4).

Gretchen's / Lamar's open tasks from ``agency_crm_tasks`` in plain English, with
the one allowed write-back: mark a task done. No IDs or jargon in the rendered
output — just the task, who owns it, what it's attached to, and when it's due.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

TASKS_TABLE = "agency_crm_tasks"

# Statuses that mean "off someone's plate". Stored lowercase in the CRM.
CLOSED_STATUSES = ["completed", "cancelled", "canceled", "done"]
_TASK_SELECT = "id,title,status,priority,due_at,assigned_to_email,case_id"


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _due_phrase(due_at: Any, today: date) -> tuple[str, int | None]:
    d = _parse_date(due_at)
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


def assignee_label(email: Any) -> str:
    """Plain-English owner from the assignee email. Never render a raw address."""
    e = str(email or "").lower()
    if "lamar" in e:
        return "Lamar"
    if "gretchen" in e:
        return "Gretchen"
    return (e.split("@")[0] or "Unassigned").title()


def shape_task(row: dict[str, Any], today: date) -> dict[str, Any]:
    due_label, due_days = _due_phrase(row.get("due_at"), today)
    return {
        "id": row.get("id"),
        "title": row.get("title") or "(untitled task)",
        "assignee": assignee_label(row.get("assigned_to_email")),
        "related": row.get("case_id"),
        "priority": row.get("priority") or "medium",
        "status": row.get("status"),
        "due_label": due_label,
        "due_days": due_days,
        "due_at": row.get("due_at"),
    }


def list_open_tasks(supa, *, today: date | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Open tasks (not completed/cancelled), most urgent first."""
    today = today or date.today()
    rows = supa.select(
        TASKS_TABLE,
        columns=_TASK_SELECT,
        params={
            "status": f"not.in.({','.join(CLOSED_STATUSES)})",
            "order": "due_at.asc.nullslast",
        },
        limit=limit,
    )
    tasks = [shape_task(r, today) for r in rows if isinstance(r, dict)]
    # tasks with no due date sort last; otherwise by urgency
    tasks.sort(key=lambda t: (t["due_days"] is None, t["due_days"] if t["due_days"] is not None else 0))
    return tasks


def group_by_assignee(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        grouped.setdefault(t["assignee"], []).append(t)
    return grouped


def complete_task(supa, task_id: str) -> dict[str, Any] | None:
    """Mark a task completed — the one allowed write-back. None if it isn't there."""
    if not task_id:
        raise ValueError("task_id is required.")
    result = supa.update(TASKS_TABLE, task_id, {"status": "completed"})
    if not result:
        return None
    return result if isinstance(result, dict) else {"id": task_id, "status": "completed"}
