"""Post agency task activity to the Nextcloud Talk team chat.

Config: ``NEXTCLOUD_TALK_TOKEN`` = the conversation token of the team chat room
(from the room URL …/call/<token>); the ``hermes`` Nextcloud user must be a
participant. If it's unset, notifications are silently skipped — task creation and
the digest never depend on chat being configured.

- ``notify_task_created`` — best-effort ping when a task/case is created.
- ``daily_task_digest`` — one scheduled post listing open tasks.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_DONE_STATUSES = {"completed", "done", "cancelled", "canceled", "closed"}


def talk_token() -> str:
    return os.environ.get("NEXTCLOUD_TALK_TOKEN", "").strip()


def _post(message: str) -> bool:
    """Post to the team chat. Returns False (never raises) if unconfigured or on error."""
    token = talk_token()
    if not token:
        return False
    try:
        from hermes.integrations.nextcloud_client import NextcloudClient

        NextcloudClient().post_talk_message(token, message)
        return True
    except Exception:  # noqa: BLE001
        log.exception("task_notify: Talk post failed")
        return False


def notify_task_created(task: dict[str, Any], *, kind: str = "task") -> bool:
    """Announce a newly created task/case in the team chat. Best-effort."""
    title = task.get("title") or task.get("subject") or "(untitled)"
    who = (task.get("assigned_to_email") or task.get("assigned_to")
           or task.get("owner_email") or "unassigned")
    client = task.get("insured_name") or task.get("client_identifier") or ""
    due = task.get("due_at") or task.get("due_date") or ""
    head = f"🆕 New {kind}: **{title}**" + (f" · {client}" if client else "")
    meta = f"→ {who}" + (f" · due {str(due)[:10]}" if due else "")
    return _post(f"{head}\n{meta}")


def daily_task_digest(supa: Any = None) -> dict[str, Any]:
    """Post a digest of open tasks to the team chat. Returns a summary dict."""
    if supa is None:
        from hermes.integrations.supabase_client import SupabaseClient

        supa = SupabaseClient()
    try:
        rows = supa.select(
            "agency_crm_tasks",
            columns="title,assigned_to_email,priority,due_at,status",
            params={"order": "due_at.asc"}, limit=200,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("task digest read failed")
        return {"ok": False, "error": str(exc)}

    open_rows = [r for r in rows if str(r.get("status") or "").lower() not in _DONE_STATUSES]
    if not open_rows:
        return {"ok": True, "count": 0, "posted": _post("📋 Task digest — no open tasks. Nice.")}

    lines = [f"📋 Open tasks ({len(open_rows)}):"]
    for r in open_rows[:20]:
        due = str(r.get("due_at") or "")[:10]
        lines.append(f"• {r.get('title') or '(untitled)'} — "
                     f"{r.get('assigned_to_email') or 'unassigned'}" + (f" (due {due})" if due else ""))
    if len(open_rows) > 20:
        lines.append(f"…and {len(open_rows) - 20} more.")
    return {"ok": True, "count": len(open_rows), "posted": _post("\n".join(lines))}
