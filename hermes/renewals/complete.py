"""Handle EspoCRM `service.task_completed` webhooks for Renewal tasks.

Wired to POST /renewals/complete (see README for the api.py snippet). EspoCRM's
ServiceWebhookDispatcher fires this when a task's status changes to Completed,
with an X-Service-Webhook-Secret header. On a renewal task completion we read the
Renewal's stage and branch:
  Renewed - Won -> file worksheet + post to #rsg-wins
  Lost          -> file worksheet + post loss reason to #the-boss
  in-flight     -> prep done, still shopping; no filing
"""
from __future__ import annotations

import hmac
import logging

from hermes.core.client import EspoClient
from hermes.integrations.slack_notifier import SlackNotifier
from hermes.documents.store import save_document

from . import config
from . import worksheet

log = logging.getLogger(__name__)


def verify_secret(header_value: str | None) -> bool:
    expected = config.SERVICE_WEBHOOK_SECRET
    if not expected:
        log.error("SERVICE_WEBHOOK_SECRET not set — rejecting webhook")
        return False
    return bool(header_value) and hmac.compare_digest(header_value, expected)


def handle(payload: dict) -> dict:
    if payload.get("eventType") != "service.task_completed":
        return {"skipped": "not a completion event"}

    task = payload.get("task", {}) or {}
    if task.get("parentType") != config.RENEWAL_ENTITY:
        return {"skipped": "not a renewal task"}

    renewal_id = task.get("parentId")
    if not renewal_id:
        return {"skipped": "no parentId"}
    task_id = task.get("id")

    espo = EspoClient()
    renewal = espo.get(f"{config.RENEWAL_ENTITY}/{renewal_id}")
    if not isinstance(renewal, dict):
        return {"skipped": "renewal not found"}
    stage = renewal.get("stage")

    if stage == config.STAGE_WON:
        return _on_won(renewal, task_id)
    if stage == config.STAGE_LOST:
        return _on_lost(renewal, task_id)
    if stage in config.IN_FLIGHT_STAGES:
        log.info("Renewal %s in-flight at '%s' — no filing.", renewal_id, stage)
        return {"stage": stage, "action": "in_flight"}

    log.info("Renewal %s task completed at non-terminal stage '%s' — no filing.",
             renewal_id, stage)
    return {"stage": stage, "action": "none"}


def _worksheet_doc(renewal: dict) -> str:
    # Worksheet body (facts / premium / checklist / outcome) + CRM links.
    body = worksheet.build_worksheet_content(renewal)
    # Links back into the CRM. Full URLs (not markdown) so Google Docs
    # auto-linkifies them into clickable links.
    links = ["\n## Links"]
    acct_url = _account_url(renewal.get("accountId"))
    ren_url = _renewal_url(renewal.get("id"))
    links.append(f"- Client record: {acct_url}" if acct_url else "- Client record: —")
    if ren_url:
        links.append(f"- Renewal worksheet (CRM): {ren_url}")
    return body + "\n".join(links) + "\n"


def _file_worksheet(renewal: dict, outcome: str) -> dict:
    # doc_type must be a VALID_DOC_TYPES value ("renewal"); the won/lost outcome
    # rides in the title + source so the index stays queryable.
    return save_document(
        title=f"{renewal.get('name', 'Renewal')} — Worksheet ({outcome})",
        content=_worksheet_doc(renewal),
        doc_type=config.DOC_TYPE_RENEWAL,
        account_name=renewal.get("accountName"),
        account_id=renewal.get("accountId"),
        source=f"hermes-renewals:{outcome}",
    )


def _task_url(task_id: str | None) -> str | None:
    base = config.ESPO_BASE_URL
    return f"{base}/#Task/view/{task_id}" if base and task_id else None


def _renewal_url(renewal_id: str | None) -> str | None:
    base = config.ESPO_BASE_URL
    return f"{base}/#{config.RENEWAL_ENTITY}/view/{renewal_id}" if base and renewal_id else None


def _account_url(account_id: str | None) -> str | None:
    base = config.ESPO_BASE_URL
    return f"{base}/#Account/view/{account_id}" if base and account_id else None


def _completion_blocks(renewal: dict, *, header: str, task_url: str | None,
                       worksheet_url: str | None) -> list[dict]:
    """Compact Slack card: client / LOB / renewal date + worksheet, task, and
    acknowledge buttons. No full description — the detail lives on the worksheet."""
    client = renewal.get("accountName") or renewal.get("name") or "—"
    lob = renewal.get("line_of_business") or "—"
    rdate = renewal.get("renewal_effective_date") or renewal.get("expiration_date") or "—"

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Client:*\n{client}"},
            {"type": "mrkdwn", "text": f"*Line of business:*\n{lob}"},
            {"type": "mrkdwn", "text": f"*Renewal date:*\n{rdate}"},
        ]},
    ]
    elements: list[dict] = []
    if worksheet_url:
        elements.append({"type": "button", "action_id": "renewal_open_worksheet",
                         "text": {"type": "plain_text", "text": "📄 Renewal Worksheet"},
                         "url": worksheet_url})
    if task_url:
        elements.append({"type": "button", "action_id": "renewal_open_task",
                         "text": {"type": "plain_text", "text": "📋 Open Task"},
                         "url": task_url})
    rid = renewal.get("id") or ""
    elements.append({"type": "button", "style": "primary", "value": rid,
                     "action_id": f"renewal_ack_{rid}",
                     "text": {"type": "plain_text", "text": "✅ Acknowledge"}})
    blocks.append({"type": "actions", "block_id": f"renewal_actions_{rid}", "elements": elements})
    return blocks


ACK_BLOCK_ID = "renewal_acked"


def apply_acknowledgement(blocks: list[dict], user_id: str) -> list[dict] | None:
    """Rewrite a completion card to its acknowledged state.

    Removes the Acknowledge button (keeps the link buttons) and appends a context
    line crediting the acknowledger. Returns None if the card is ALREADY
    acknowledged (presence of the ``renewal_acked`` block) so repeated clicks are
    idempotent no-ops — the button only ever fires once.
    """
    blocks = blocks or []
    if any(b.get("block_id") == ACK_BLOCK_ID for b in blocks):
        return None

    out: list[dict] = []
    for b in blocks:
        if b.get("type") == "actions":
            kept = [e for e in b.get("elements", [])
                    if not str(e.get("action_id", "")).startswith("renewal_ack_")]
            if kept:
                nb = dict(b)
                nb["elements"] = kept
                out.append(nb)
            # if only the ack button was there, drop the whole actions block
        else:
            out.append(b)

    out.append({
        "type": "context",
        "block_id": ACK_BLOCK_ID,
        "elements": [{"type": "mrkdwn", "text": f":white_check_mark: Acknowledged by <@{user_id}>"}],
    })
    return out


def _worksheet_url(renewal: dict, doc: dict | None) -> str | None:
    # Prefer the freshly-filed Google Doc; fall back to the live Renewal record.
    return (doc or {}).get("drive_url") or _renewal_url(renewal.get("id"))


def _on_won(renewal: dict, task_id: str | None = None) -> dict:
    doc = _file_worksheet(renewal, "won")
    client = renewal.get("accountName") or renewal.get("name") or "this client"
    blocks = _completion_blocks(
        renewal,
        header=f"✅ *Renewal retained — {client}*",
        task_url=_task_url(task_id),
        worksheet_url=_worksheet_url(renewal, doc),
    )
    try:
        SlackNotifier(channel=config.SLACK_RSG_WINS).post_message(
            text=f"Renewal retained — {client} ({renewal.get('line_of_business', '')})",
            blocks=blocks,
        )
    except Exception as e:
        log.warning("Win notify failed: %s", e)
    return {"stage": config.STAGE_WON, "filed": bool(doc), "action": "won"}


def _on_lost(renewal: dict, task_id: str | None = None) -> dict:
    doc = _file_worksheet(renewal, "lost")
    client = renewal.get("accountName") or renewal.get("name") or "this client"
    reason = renewal.get("lost_reason") or "—"
    blocks = _completion_blocks(
        renewal,
        header=f"❌ *Renewal lost — {client}*\nReason: *{reason}*",
        task_url=_task_url(task_id),
        worksheet_url=_worksheet_url(renewal, doc),
    )
    try:
        SlackNotifier(channel=config.SLACK_THE_BOSS).post_message(
            text=f"Renewal lost — {client} — {reason}",
            blocks=blocks,
        )
    except Exception as e:
        log.warning("Loss notify failed: %s", e)
    return {"stage": config.STAGE_LOST, "filed": bool(doc), "action": "lost"}
