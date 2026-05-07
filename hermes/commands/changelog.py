"""Dispatcher-routed CRM changelog command.

Allows querying recent CRM changes from Slack or Open WebUI.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient

_HOURS_RE = re.compile(r"\b(\d+)\s*(?:hours?|hrs?|h)\b", re.I)


def handle(client: "EspoClient", text: str) -> DispatchResult:
    """Return recent CRM changes."""
    hours_match = _HOURS_RE.search(text)
    lookback_hours = int(hours_match.group(1)) if hours_match else 24

    try:
        from hermes.jobs.nightly_changelog import run_on_demand

        result = run_on_demand(client, lookback_hours=lookback_hours)
        return DispatchResult(result.ok, result.message)
    except Exception as exc:
        return DispatchResult(False, f"Changelog query failed: {exc}")
