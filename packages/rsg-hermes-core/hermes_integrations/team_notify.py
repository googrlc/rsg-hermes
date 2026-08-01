"""Team notifications over Nextcloud Talk — the transport Hermes reports post to.

Replaces the old Slack posting path. Reports still call the same
``SlackNotifier(channel=...).post_message(text=, blocks=)`` API (see
``slack_notifier``); this module does the real work: resolve the Slack channel
id to a Talk room token, render the Block Kit payload to markdown, and post via
``NextcloudClient.post_talk_message``.

Room routing is env-driven (tokens from the RSG report rooms):
    HERMES_TALK_ROOM_BOSS       #the-boss  → owner-facing reports
    HERMES_TALK_ROOM_RENEWALS   #renewal-updates → Gretchen renewal lists
    HERMES_TALK_ROOM_SYSTEMS    #systems-check → error/health alerts
An unmapped channel falls back to the boss room, so no report is silently lost.
"""
from __future__ import annotations

import os
import re
from typing import Any

# LEGACY Slack channel id → category. Retained only for already-deployed callers
# that still pass an id; new code should pass the category name directly.
_CHANNEL_CATEGORY: dict[str, str] = {
    "C0ANQUENX4P": "boss",       # #the-boss
    "C09R2CG2KS6": "renewals",   # #renewal-updates
    "C0B6MPN1U3U": "systems",    # #systems-check
}
_CATEGORY_ENV = {
    "boss": "HERMES_TALK_ROOM_BOSS",
    "renewals": "HERMES_TALK_ROOM_RENEWALS",
    "systems": "HERMES_TALK_ROOM_SYSTEMS",
}


class TeamNotifyError(RuntimeError):
    """Raised when a report can't be delivered to Talk."""


def resolve_room(channel: str | None) -> str:
    """Map a category name OR a legacy Slack channel id to a Talk room token.

    Accepting the category name is the fix for a live misroute: every caller that
    passed "systems" or "renewals" — the obvious thing to pass — fell through to
    the boss-room default, because only three hardcoded Slack ids were mapped. The
    two systems-check defaults in the tree (C0ANSEP6SSD in renewals/config.py,
    C0AFHN83ZE3 in scheduler/runner.py) are not among those three, so scheduler
    alerts and renewal escalations were ALL landing in the boss room.

    Unknown values still fall back to boss rather than raising: losing a report to
    a routing typo is worse than posting it somewhere visible.
    """
    key = (channel or "").strip()
    category = key if key in _CATEGORY_ENV else _CHANNEL_CATEGORY.get(key, "boss")
    token = os.environ.get(_CATEGORY_ENV[category], "").strip()
    return token or os.environ.get("HERMES_TALK_ROOM_BOSS", "").strip()


# -- Block Kit → markdown ---------------------------------------------------

def _slack_to_md(text: str) -> str:
    """Convert Slack mrkdwn to Talk-friendly markdown (bold + links)."""
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"[\2](\1)", text)   # <url|label>
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)                    # <url>
    text = re.sub(r"(?<![*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*])", r"**\1**", text)  # *bold* → **bold**
    return text


def render_blocks_to_markdown(text: str, blocks: list[dict[str, Any]] | None) -> str:
    """Render a Slack payload to markdown. Falls back to ``text`` when there are
    no blocks (the fallback summary callers always provide)."""
    if not blocks:
        return _slack_to_md(text or "")
    lines: list[str] = []
    for b in blocks:
        btype = b.get("type")
        if btype == "divider":
            lines.append("\n---")
        elif btype == "header":
            lines.append(f"\n## {(b.get('text') or {}).get('text', '').strip()}")
        elif btype == "section":
            txt = (b.get("text") or {}).get("text")
            if txt:
                lines.append(_slack_to_md(txt))
            for f in b.get("fields") or []:
                ftxt = f.get("text")
                if ftxt:
                    lines.append(_slack_to_md(ftxt))
        elif btype == "context":
            for el in b.get("elements") or []:
                if el.get("text"):
                    lines.append(f"_{_slack_to_md(el['text'])}_")
    body = "\n".join(l for l in lines).strip()
    return body or _slack_to_md(text or "")


class TeamNotifier:
    """Posts a report to a Nextcloud Talk room resolved from a Slack channel id."""

    def __init__(self, *, channel: str | None = None, **_ignored: Any) -> None:
        # Extra kwargs (bot_token, retry_*, client) are accepted + ignored so the
        # old SlackNotifier call sites keep working unchanged during the cutover.
        self.channel = channel

    def post_message(self, *, text: str, blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        room = resolve_room(self.channel)
        if not room:
            raise TeamNotifyError(
                "No Talk room configured — set HERMES_TALK_ROOM_BOSS (and RENEWALS/SYSTEMS)."
            )
        message = render_blocks_to_markdown(text, blocks)
        try:
            from hermes_integrations.nextcloud_client import NextcloudClient

            NextcloudClient().post_talk_message(room, message)
        except Exception as exc:  # noqa: BLE001
            raise TeamNotifyError(f"Failed to post report to Talk room {room}: {exc}") from exc
        return {"ok": True, "room": room}
