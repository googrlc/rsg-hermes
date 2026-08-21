"""Forward a policy investigation case to the Cursor Data Quality Investigator automation."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)


class CursorDqiTriggerError(Exception):
    """Cursor automation webhook rejected the request."""


def _cursor_webhook_config() -> tuple[str, str]:
    url = (os.environ.get("CURSOR_AUTOMATION_WEBHOOK_URL") or "").strip()
    key = (os.environ.get("CURSOR_AUTOMATION_WEBHOOK_KEY") or "").strip()
    if not url or not key:
        raise CursorDqiTriggerError(
            "CURSOR_AUTOMATION_WEBHOOK_URL and CURSOR_AUTOMATION_WEBHOOK_KEY must be set on Hermes"
        )
    return url, key


def trigger_cursor_dqi_investigation(payload: dict[str, Any]) -> dict[str, Any]:
    """POST JSON to the Cursor automation webhook. Returns parsed JSON when possible."""
    url, key = _cursor_webhook_config()
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise CursorDqiTriggerError(f"Cursor webhook HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise CursorDqiTriggerError(f"Cursor webhook unreachable: {exc}") from exc

    if not raw.strip():
        return {"status": "accepted", "raw": ""}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "accepted", "raw": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"status": "accepted", "raw": parsed}
