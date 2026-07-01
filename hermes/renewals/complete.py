"""Handle EspoCRM `service.task_completed` webhooks for Renewal tasks."""
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
    _ensure_display_fields(espo, renewal)
    _ensure_worksheet(espo, renewal)
    pipeline_stage = _pipeline_stage(renewal)
    disposition = _disposition(renewal)

    if disposition in config.WIN_DISPOSITIONS:
        return _on_won(renewal, task_id, disposition=disposition)
    if disposition in config.LOSS_DISPOSITIONS:
        return _on_lost(renewal, task_id, disposition=disposition)
    if pipeline_stage in config.IN_FLIGHT_STAGES:
        log.info("Renewal %s in-flight at '%s' — no filing.", renewal_id, pipeline_stage)
        return {"pipeline_stage": pipeline_stage, "disposition": disposition, "action": "in_flight"}

    log.info("Renewal %s task completed at non-terminal stage '%s' — no filing.",
             renewal_id, pipeline_stage)
    return {"pipeline_stage": pipeline_stage, "disposition": disposition, "action": "none"}


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


def _client_name(renewal: dict) -> str:
    """Best client label for the card.

    Prefer the linked account's name. Never fall back to the Renewal's own name as
    the client — RenewalOrchestrator builds it as "{account} - {LOB} Renewal", so
    using it whole reads wrong (the screenshot bug: "Dream Chaser Trucking - Other
    Renewal" shown as the client). As a last resort, strip that suffix back to the
    account portion.
    """
    acct = (renewal.get("accountName") or "").strip()
    if acct:
        return acct
    name = (renewal.get("name") or "").strip()
    if " - " in name:  # "{account} - {LOB} Renewal"
        return name.split(" - ", 1)[0].strip() or "this client"
    return name or "this client"


def _lob(renewal: dict) -> str:
    return (renewal.get("line_of_business") or "").strip() or "—"


def _renewal_date(renewal: dict) -> str:
    return renewal.get("renewal_effective_date") or renewal.get("expiration_date") or "—"


def _pipeline_stage(renewal: dict) -> str | None:
    return renewal.get("pipeline_stage") or renewal.get("stage")


def _disposition(renewal: dict) -> str | None:
    return renewal.get("disposition") or (
        config.DISPOSITION_WON if renewal.get("stage") == config.STAGE_WON else
        config.DISPOSITION_LOST if renewal.get("stage") == config.STAGE_LOST else None
    )


def _loss_reason(renewal: dict, disposition: str | None = None) -> str:
    value = renewal.get("lost_reason")
    if value:
        return str(value)
    disposition = disposition or _disposition(renewal)
    if disposition == config.DISPOSITION_DO_NOT_RENEW:
        return "Do not renew"
    if disposition:
        return disposition.replace("_", " ").title()
    return "—"


def _ensure_display_fields(espo, renewal: dict) -> None:
    """Backfill the card's display fields if a single-record fetch didn't carry
    them. A full Renewal GET normally returns accountName + line_of_business, but
    if accountName is empty while accountId is set, resolve it via the Account so
    the card never falls back to the renewal name (see _client_name)."""
    if (renewal.get("accountName") or "").strip():
        return
    acct_id = renewal.get("accountId")
    if not acct_id:
        return
    try:
        acct = espo.get(f"Account/{acct_id}")
        if isinstance(acct, dict) and acct.get("name"):
            renewal["accountName"] = acct["name"]
    except Exception as e:  # display nicety only — never fail the webhook over it
        log.debug("Account name backfill failed for %s: %s", acct_id, e)


def _ensure_worksheet(espo, renewal: dict) -> None:
    if worksheet.worksheet_record(renewal):
        return

    for key in config.WORKSHEET_ID_KEYS:
        worksheet_id = renewal.get(key)
        if not worksheet_id:
            continue
        try:
            row = espo.get(f"{config.RENEWAL_WORKSHEET_ENTITY}/{worksheet_id}")
            if isinstance(row, dict) and _looks_like_worksheet(row):
                renewal["renewalWorksheet"] = row
                return
        except Exception as e:
            log.debug("Worksheet lookup failed for %s: %s", worksheet_id, e)

    renewal_id = renewal.get("id")
    if not renewal_id:
        return
    for link_name in ("renewalWorksheet", "worksheet"):
        try:
            row = espo.get(f"{config.RENEWAL_ENTITY}/{renewal_id}/{link_name}")
            if isinstance(row, dict) and _looks_like_worksheet(row):
                renewal["renewalWorksheet"] = row
                return
            if isinstance(row, list) and row and isinstance(row[0], dict) and _looks_like_worksheet(row[0]):
                renewal["renewalWorksheet"] = row[0]
                return
        except Exception as e:
            log.debug("Worksheet link lookup failed for %s/%s: %s", renewal_id, link_name, e)


def _looks_like_worksheet(row: dict) -> bool:
    if row.get("lob_variant") is not None or row.get("completion_type") is not None:
        return True
    return any(field in row for field in config.CHECKBOX_FIELDS) and "pipeline_stage" not in row


def build_renewal_card(renewal: dict, *, header: str, task_url: str | None,
                       worksheet_url: str | None) -> list[dict]:
    """Compact Slack card: client / LOB / renewal date + worksheet, task, and
    acknowledge buttons. No full description — the detail lives on the worksheet.

    Shared by the renewal sweep (task ready -> #gretchen-tasks) and the completion
    webhook (won -> #rsg-wins, lost -> #the-boss) so every renewal card looks the
    same and the client/LOB mapping lives in one place."""
    client = _client_name(renewal)
    lob = _lob(renewal)
    rdate = _renewal_date(renewal)

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


# Back-compat alias (referenced by tests and earlier call sites).
_completion_blocks = build_renewal_card


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


def _on_won(renewal: dict, task_id: str | None = None, *, disposition: str | None = None) -> dict:
    doc = _file_worksheet(renewal, "won")
    client = _client_name(renewal)
    disposition = disposition or _disposition(renewal)
    retained_label = "rewritten" if disposition == config.DISPOSITION_REWRITTEN else "retained"
    blocks = build_renewal_card(
        renewal,
        header=f"✅ *Renewal {retained_label} — {client}*",
        task_url=_task_url(task_id),
        worksheet_url=_worksheet_url(renewal, doc),
    )
    try:
        SlackNotifier(channel=config.SLACK_RSG_WINS).post_message(
            text=f"Renewal {retained_label} — {client} ({renewal.get('line_of_business', '')})",
            blocks=blocks,
        )
    except Exception as e:
        log.warning("Win notify failed: %s", e)
    return {
        "pipeline_stage": _pipeline_stage(renewal),
        "disposition": disposition,
        "filed": bool(doc),
        "action": config.DISPOSITION_REWRITTEN if disposition == config.DISPOSITION_REWRITTEN else "won",
    }


def _on_lost(renewal: dict, task_id: str | None = None, *, disposition: str | None = None) -> dict:
    doc = _file_worksheet(renewal, "lost")
    client = _client_name(renewal)
    disposition = disposition or _disposition(renewal)
    reason = _loss_reason(renewal, disposition)
    blocks = build_renewal_card(
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
    return {
        "pipeline_stage": _pipeline_stage(renewal),
        "disposition": disposition,
        "filed": bool(doc),
        "action": "lost",
    }
