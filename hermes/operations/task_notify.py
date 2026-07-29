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

from hermes.core import surfaces

log = logging.getLogger(__name__)

_DONE_STATUSES = {"completed", "done", "cancelled", "canceled", "closed"}

# Priority → emoji, so urgency reads at a glance in the chat line.
_PRIORITY_EMOJI = {"urgent": "🔴", "high": "🔴", "medium": "🟡", "normal": "🟡", "low": "🟢"}


def talk_token() -> str:
    return os.environ.get("NEXTCLOUD_TALK_TOKEN", "").strip()


def _crm_link(view: str = "tasks") -> str:
    """A markdown link back into the CRM, or "" if no portal URL is set.

    Points at the RSG Agency Portal (``HERMES_PORTAL_URL``), which is the CRM —
    not at this API's own origin, which used to serve the cockpit and now serves
    no screen at all.

    The portal is a single-page app with no URL routing, so ``view`` cannot be
    honoured: there is no address that opens it on Tasks. The link therefore
    says "open the CRM" rather than naming a destination it does not reach —
    the alternative is a link that lands somewhere else and looks broken."""
    base = surfaces.portal_url()
    if not base:
        return ""
    return f"[open the CRM ↗]({base}/)"


def _priority_badge(priority: Any) -> str:
    p = str(priority or "").lower()
    return f"{_PRIORITY_EMOJI.get(p, '⚪')} {p}" if p else ""


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
    due = str(task.get("due_at") or task.get("due_date") or "")[:10]
    desc = str(task.get("description") or "").strip()

    lines = [f"🆕 **New {kind}:** {title}"]
    facts = [f"🏢 {client}" if client else "", f"👤 {who}",
             f"📅 due {due}" if due else "", _priority_badge(task.get("priority"))]
    lines.append(" · ".join(f for f in facts if f))
    if desc:
        lines.append(f"📝 {desc[:280]}")
    link = _crm_link("tasks")
    if link:
        lines.append(f"🔗 {link}")
    return _post("\n".join(lines))


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
        return {"ok": True, "count": 0, "posted": _post("📋 **Task digest** — no open tasks. Nice. 🎉")}

    link = _crm_link("tasks")
    lines = [f"📋 **Open tasks ({len(open_rows)})**" + (f"  ·  {link}" if link else ""), ""]
    for r in open_rows[:20]:
        due = str(r.get("due_at") or "")[:10]
        facts = [f"👤 {r.get('assigned_to_email') or 'unassigned'}",
                 f"📅 {due}" if due else "", _priority_badge(r.get("priority"))]
        lines.append(f"🔹 **{r.get('title') or '(untitled)'}**")
        lines.append("    " + " · ".join(f for f in facts if f))
    if len(open_rows) > 20:
        lines.append(f"…and {len(open_rows) - 20} more.")
    return {"ok": True, "count": len(open_rows), "posted": _post("\n".join(lines))}
