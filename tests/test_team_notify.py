"""Reports → Nextcloud Talk transport (replaces Slack posting)."""
from __future__ import annotations

import pytest

from hermes.integrations import team_notify as T
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError


def _rooms(monkeypatch):
    monkeypatch.setenv("HERMES_TALK_ROOM_BOSS", "boss1")
    monkeypatch.setenv("HERMES_TALK_ROOM_RENEWALS", "ren1")
    monkeypatch.setenv("HERMES_TALK_ROOM_SYSTEMS", "sys1")


def test_resolve_room_maps_known_channels(monkeypatch):
    _rooms(monkeypatch)
    assert T.resolve_room("C0ANQUENX4P") == "boss1"     # #the-boss
    assert T.resolve_room("C09R2CG2KS6") == "ren1"      # #renewal-updates
    assert T.resolve_room("C0B6MPN1U3U") == "sys1"      # #systems-check


def test_resolve_room_unknown_falls_back_to_boss(monkeypatch):
    _rooms(monkeypatch)
    assert T.resolve_room("C0WHATEVER") == "boss1"      # never silently lost
    assert T.resolve_room(None) == "boss1"


def test_render_blocks_to_markdown():
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Sentinel"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*3* stale leads"}},
        {"type": "divider"},
        {"type": "section", "fields": [{"type": "mrkdwn", "text": "Acme — <https://x/1|open>"}]},
    ]
    md = T.render_blocks_to_markdown("fallback", blocks)
    assert "## Sentinel" in md
    assert "**3** stale leads" in md          # slack *bold* → markdown **bold**
    assert "---" in md
    assert "[open](https://x/1)" in md        # <url|label> → [label](url)


def test_render_falls_back_to_text_without_blocks():
    assert T.render_blocks_to_markdown("just text", None) == "just text"


def test_notifier_posts_to_resolved_room(monkeypatch):
    _rooms(monkeypatch)
    sent = {}

    class FakeNC:
        def post_talk_message(self, token, message):
            sent["token"] = token
            sent["message"] = message

    import hermes.integrations.nextcloud_client as nc
    monkeypatch.setattr(nc, "NextcloudClient", lambda *a, **k: FakeNC())

    res = SlackNotifier(channel="C0ANQUENX4P").post_message(text="hi", blocks=None)
    assert res["ok"] and sent["token"] == "boss1" and sent["message"] == "hi"


def test_notifier_raises_slack_error_when_no_room(monkeypatch):
    monkeypatch.delenv("HERMES_TALK_ROOM_BOSS", raising=False)
    monkeypatch.delenv("HERMES_TALK_ROOM_SYSTEMS", raising=False)
    with pytest.raises(SlackNotifierError):
        SlackNotifier(channel="C0B6MPN1U3U").post_message(text="x")
