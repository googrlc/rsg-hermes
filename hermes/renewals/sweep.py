"""Renewal sweep: turn freshly-created Renewal records into tasks for Gretchen.

n8n WF1 already creates the Renewal record (stage=Identified) at the LOB-specific
expiry threshold and pre-fills the expiring premium. This sweep attaches the task
+ card and routes it to Gretchen. It does NOT touch stage — that stays Gretchen's
signal of real progress.

Run from cron, e.g.:  0 6 * * 1-5  hermes --renewal-sweep
Safe first run (one task only):
    python -c "from hermes.renewals.sweep import run; print(run(limit=1))"
"""
from __future__ import annotations

import logging

from hermes.core.client import EspoClient

from . import config
from .tasks import _rows, resolve_gretchen_id, has_existing_task, create_renewal_task

log = logging.getLogger(__name__)

# EspoCRM Renewal custom fields are snake_case at the API layer (confirmed live:
# current_premium, expiration_date, line_of_business — NOT camelCase).
_SELECT = (
    "id,name,accountId,accountName,contactId,carrier,line_of_business,"
    "current_premium,expiration_date,renewal_effective_date,urgency"
)

# Hard ceiling so a misconfigured run can never task the entire backlog unobserved.
_MAX_CANDIDATES = 200


def run(limit: int | None = None) -> dict:
    """Create renewal tasks for Identified renewals lacking one.

    limit: cap the number of candidates processed (use limit=1 for a safe first
    live run). None = process all (capped at _MAX_CANDIDATES for safety).
    """
    espo = EspoClient()
    gretchen_id = resolve_gretchen_id(espo)

    body = espo.get(config.RENEWAL_ENTITY, params={
        "maxSize": limit or _MAX_CANDIDATES,
        "select": _SELECT,
        "orderBy": "expiration_date",
        "order": "asc",
        "where": [
            {"type": "equals", "attribute": "stage", "value": config.STAGE_IDENTIFIED},
        ],
    })
    renewals = _rows(body)
    if limit:
        renewals = renewals[:limit]

    created = []  # list of (renewal, task) pairs — task carries the id for the card's "Open Task" button
    for r in renewals:
        try:
            if has_existing_task(espo, r["id"]):
                continue
            task = create_renewal_task(espo, r, gretchen_id)
            if task:
                created.append((r, task))
        except Exception:  # one bad record shouldn't kill the sweep
            log.exception("Failed to create task for Renewal %s", r.get("id"))

    _notify(created)
    log.info("Renewal sweep: %d task(s) created of %d candidate(s)",
             len(created), len(renewals))
    return {"candidates": len(renewals), "created": len(created)}


def _notify(created: list) -> None:
    """Post one rich card per new renewal to #gretchen-tasks.

    Same compact card the won/lost webhook posts (Client / LOB / Renewal date +
    📄 Renewal Worksheet · 📋 Open Task · ✅ Acknowledge), so every renewal looks
    the same end-to-end. The worksheet button deep-links the Renewal record (no
    Google Doc is filed until completion); the task button opens the new task.
    """
    if not created:
        return
    # Imported lazily so the sweep can run (and be unit-tested) without Slack creds.
    from hermes.integrations.slack_notifier import SlackNotifier
    from .complete import build_renewal_card, _client_name, _task_url, _renewal_url

    notifier = SlackNotifier(channel=config.SLACK_GRETCHEN_TASKS)

    # Lead with a one-line digest so a multi-renewal sweep is still scannable...
    try:
        notifier.post_message(text=f"*{len(created)} renewal task(s) ready*")
    except Exception as e:  # Slack is a nice-to-have, never fatal
        log.warning("Renewal sweep Slack digest failed: %s", e)

    # ...then a card per renewal, each independently actionable.
    for renewal, task in created:
        client = _client_name(renewal)
        blocks = build_renewal_card(
            renewal,
            header=f"📋 *Renewal task ready — {client}*",
            task_url=_task_url((task or {}).get("id")),
            worksheet_url=_renewal_url(renewal.get("id")),
        )
        try:
            notifier.post_message(
                text=f"Renewal task ready — {client}",
                blocks=blocks,
            )
        except Exception as e:  # one card failing must not stop the rest
            log.warning("Renewal sweep card failed for %s: %s", renewal.get("id"), e)
