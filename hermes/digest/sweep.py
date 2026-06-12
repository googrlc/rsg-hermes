"""Read-only queries for the morning digest.

Policy fields are snake_case at the API layer (confirmed live);
Opportunity and Task are camelCase (confirmed live).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from hermes.core.client import EspoClient

from . import config


def _rows(body) -> list[dict]:
    if isinstance(body, dict):
        return body.get("list", []) or []
    return []


def expiring_policies(espo: EspoClient) -> list[dict]:
    today = date.today()
    horizon = today + timedelta(days=config.RENEWAL_HORIZON_DAYS)
    body = espo.get("Policy", params={
        "maxSize": 200,
        "orderBy": "expiration_date",
        "order": "asc",
        "select": ("id,name,accountId,accountName,expiration_date,"
                   "premium_amount,line_of_business,carrier,status"),
        "where": [
            {"type": "in", "attribute": "status",
             "value": config.ACTIVE_POLICY_STATUSES},
            {"type": "greaterThanOrEquals", "attribute": "expiration_date",
             "value": today.isoformat()},
            {"type": "lessThanOrEquals", "attribute": "expiration_date",
             "value": horizon.isoformat()},
        ],
    })
    return _rows(body)


def quiet_opportunities(espo: EspoClient) -> list[dict]:
    """Open opps with no record activity for QUIET_DAYS+ days."""
    cutoff = datetime.utcnow() - timedelta(days=config.QUIET_DAYS)
    body = espo.get("Opportunity", params={
        "maxSize": 200,
        "orderBy": "modifiedAt",
        "order": "asc",
        "select": ("id,name,accountName,stage,estimatedPremium,amount,"
                   "lineOfBusiness,modifiedAt,assignedUserName,"
                   "skipEmailSequence,closeDate"),
        "where": [
            {"type": "before", "attribute": "modifiedAt",
             "value": cutoff.strftime("%Y-%m-%d %H:%M:%S")},
        ],
    })
    out = []
    for o in _rows(body):
        stage = (o.get("stage") or "").lower()
        if any(k in stage for k in config.TERMINAL_KEYWORDS):
            continue
        if o.get("skipEmailSequence"):
            continue
        mod = o.get("modifiedAt") or ""
        try:
            days = (datetime.utcnow()
                    - datetime.strptime(mod, "%Y-%m-%d %H:%M:%S")).days
        except ValueError:
            days = config.QUIET_DAYS
        o["_quiet_days"] = days
        out.append(o)
    return out


def overdue_tasks(espo: EspoClient) -> list[dict]:
    body = espo.get("Task", params={
        "maxSize": 100,
        "orderBy": "dateEnd",
        "order": "asc",
        "select": "id,name,status,dateEnd,assignedUserName,parentType,parentName",
        "where": [
            {"type": "notIn", "attribute": "status",
             "value": config.OPEN_TASK_EXCLUDE},
            {"type": "before", "attribute": "dateEnd",
             "value": date.today().isoformat()},
            {"type": "isNotNull", "attribute": "dateEnd"},
        ],
    })
    return _rows(body)


def collect(espo: EspoClient | None = None) -> dict:
    espo = espo or EspoClient()
    return {
        "generated": datetime.now().strftime("%A, %B %-d %Y %I:%M %p"),
        "policies": expiring_policies(espo),
        "quiet": quiet_opportunities(espo),
        "tasks": overdue_tasks(espo),
    }
