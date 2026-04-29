"""Cross-sell / renewal oriented views (Opportunities)."""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


def _open_opportunities(client: EspoClient, limit: int = 15) -> list[dict[str, Any]]:
    """Recent opportunities; add a `where` filter when your pipeline stage names are known."""
    body = client.get(
        "Opportunity",
        params={
            "maxSize": limit,
            "orderBy": [["modifiedAt", "desc"]],
        },
    )
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [x for x in body["list"] if isinstance(x, dict)]
    return []


def _window_rows(client: EspoClient, start_days: int, end_days: int) -> list[dict[str, Any]]:
    entity = os.environ.get("HERMES_RENEWAL_ENTITY", "Opportunity")
    date_field = os.environ.get("HERMES_RENEWAL_DATE_FIELD", "closeDate")
    start = (date.today() + timedelta(days=start_days)).isoformat()
    end = (date.today() + timedelta(days=end_days)).isoformat()
    body = client.get(
        entity,
        params={
            "maxSize": 100,
            "orderBy": [[date_field, "asc"]],
            "select": f"id,name,amount,stage,{date_field},accountName",
            "where": [
                {"type": "greaterThanOrEquals", "attribute": date_field, "value": start},
                {"type": "lessThanOrEquals", "attribute": date_field, "value": end},
            ],
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def renewal_audit(client: EspoClient) -> DispatchResult:
    windows = ((0, 30), (31, 60), (61, 90))
    lines = ["Renewal audit:"]
    data: dict[str, Any] = {}
    for start, end in windows:
        rows = _window_rows(client, start, end)
        key = f"{start}-{end}"
        data[key] = rows
        lines.append(f"{start}-{end} days: {len(rows)}")
        for row in rows[:5]:
            name = row.get("name", "?")
            account = row.get("accountName") or ""
            close = row.get(os.environ.get("HERMES_RENEWAL_DATE_FIELD", "closeDate"), "?")
            stage = row.get("stage", "?")
            lines.append(f"- {name} | {account} | {stage} | {close}")
        if len(rows) > 5:
            lines.append(f"- ... {len(rows) - 5} more")
    return DispatchResult(True, "\n".join(lines), data)


def handle(client: EspoClient, text: str) -> DispatchResult:
    _ = text  # reserved for filters, e.g. renewal Q2
    if re.search(r"\b(expir(?:e|ing|y)|renewal[-\s]?audit)\b", text, re.I):
        return renewal_audit(client)
    if re.search(r"renewal", text, re.I):
        msg = "Renewal pipeline (open stages, by close date):"
    elif re.search(r"cross", text, re.I):
        msg = "Cross-sell / open opportunities:"
    else:
        msg = "Open opportunities:"

    rows = _open_opportunities(client)
    if not rows:
        return DispatchResult(True, f"{msg}\n(none in default open stages — tune stages in revenue.py)", {"rows": []})

    lines = [msg]
    for r in rows:
        name = r.get("name", "?")
        amt = r.get("amount")
        stage = r.get("stage", "?")
        close = r.get("closeDate", "?")
        lines.append(f"- {name} | {amt} | {stage} | close {close}")
    return DispatchResult(True, "\n".join(lines), {"rows": rows})
