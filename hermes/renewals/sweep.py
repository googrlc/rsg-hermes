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

_SELECT = (
    "id,name,accountId,accountName,contactId,carrier,lineOfBusiness,"
    "currentPremium,expirationDate,urgency"
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
        "orderBy": "expirationDate",
        "order": "asc",
        "where": [
            {"type": "equals", "attribute": "stage", "value": config.STAGE_IDENTIFIED},
        ],
    })
    renewals = _rows(body)
    if limit:
        renewals = renewals[:limit]

    created = []
    for r in renewals:
        try:
            if has_existing_task(espo, r["id"]):
                continue
            task = create_renewal_task(espo, r, gretchen_id)
            if task:
                created.append(r)
        except Exception:  # one bad record shouldn't kill the sweep
            log.exception("Failed to create task for Renewal %s", r.get("id"))

    _notify(created)
    log.info("Renewal sweep: %d task(s) created of %d candidate(s)",
             len(created), len(renewals))
    return {"candidates": len(renewals), "created": len(created)}


def _notify(created: list) -> None:
    if not created:
        return
    # Imported lazily so the sweep can run (and be unit-tested) without Slack creds.
    from hermes.integrations.slack_notifier import SlackNotifier

    lines = [f"*{len(created)} renewal task(s) ready*"]
    for r in created:
        acct = r.get("accountName") or r.get("name")
        lines.append(
            f"• {acct} — {r.get('lineOfBusiness', '')} — expires {r.get('expirationDate', '')}"
        )
    try:
        SlackNotifier(channel=config.SLACK_GRETCHEN_TASKS).post_message(text="\n".join(lines))
    except Exception as e:  # Slack is a nice-to-have, never fatal
        log.warning("Renewal sweep Slack notify failed: %s", e)
