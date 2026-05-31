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

    espo = EspoClient()
    renewal = espo.get(f"{config.RENEWAL_ENTITY}/{renewal_id}")
    if not isinstance(renewal, dict):
        return {"skipped": "renewal not found"}
    stage = renewal.get("stage")

    if stage == config.STAGE_WON:
        return _on_won(renewal)
    if stage == config.STAGE_LOST:
        return _on_lost(renewal)
    if stage in config.IN_FLIGHT_STAGES:
        log.info("Renewal %s in-flight at '%s' — no filing.", renewal_id, stage)
        return {"stage": stage, "action": "in_flight"}

    log.info("Renewal %s task completed at non-terminal stage '%s' — no filing.",
             renewal_id, stage)
    return {"stage": stage, "action": "none"}


def _worksheet_doc(renewal: dict) -> str:
    name = renewal.get("name") or renewal.get("accountName") or "Renewal"
    return (
        f"# {name}\n\n"
        f"- Carrier: {renewal.get('carrier', '—')}\n"
        f"- Line of business: {renewal.get('lineOfBusiness', '—')}\n"
        f"- Expiring premium: {renewal.get('currentPremium', '—')}\n"
        f"- Renewal premium: {renewal.get('renewalPremium', '—')}\n"
        f"- Premium change: {renewal.get('premiumChange', '—')}%\n"
        f"- Outcome: {renewal.get('stage', '—')}\n"
        f"- Lost reason: {renewal.get('lostReason', '—')}\n"
        f"- Client states: {renewal.get('renewalNotes', '—')}\n"
        f"- Renewal effective date: {renewal.get('renewalEffectiveDate', '—')}\n"
    )


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


def _on_won(renewal: dict) -> dict:
    doc = _file_worksheet(renewal, "won")
    acct = renewal.get("accountName") or renewal.get("name")
    try:
        SlackNotifier(channel=config.SLACK_RSG_WINS).post_message(
            text=(f"*Renewal retained — {acct}*\n"
                  f"{renewal.get('lineOfBusiness', '')} · renewal premium "
                  f"{renewal.get('renewalPremium', '—')} "
                  f"({renewal.get('premiumChange', '—')}% change)")
        )
    except Exception as e:
        log.warning("Win notify failed: %s", e)
    return {"stage": config.STAGE_WON, "filed": bool(doc), "action": "won"}


def _on_lost(renewal: dict) -> dict:
    doc = _file_worksheet(renewal, "lost")
    acct = renewal.get("accountName") or renewal.get("name")
    try:
        SlackNotifier(channel=config.SLACK_THE_BOSS).post_message(
            text=(f"*Renewal lost — {acct}*\n"
                  f"{renewal.get('lineOfBusiness', '')} · reason: "
                  f"*{renewal.get('lostReason', '—')}*\n"
                  f"Client states: {renewal.get('renewalNotes', '—')}")
        )
    except Exception as e:
        log.warning("Loss notify failed: %s", e)
    return {"stage": config.STAGE_LOST, "filed": bool(doc), "action": "lost"}
