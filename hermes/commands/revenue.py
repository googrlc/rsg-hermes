"""Cross-sell / renewal oriented views (Opportunities)."""

from __future__ import annotations

import re
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


def handle(client: EspoClient, text: str) -> DispatchResult:
    _ = text  # reserved for filters, e.g. renewal Q2
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
