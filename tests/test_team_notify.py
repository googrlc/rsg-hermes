"""Reports → Nextcloud Talk transport (replaces Slack posting)."""
from __future__ import annotations

import pytest

from hermes_integrations import team_notify as T
from hermes_integrations.slack_notifier import SlackNotifier, SlackNotifierError


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

    import hermes_integrations.nextcloud_client as nc
    monkeypatch.setattr(nc, "NextcloudClient", lambda *a, **k: FakeNC())

    res = SlackNotifier(channel="C0ANQUENX4P").post_message(text="hi", blocks=None)
    assert res["ok"] and sent["token"] == "boss1" and sent["message"] == "hi"


def test_notifier_raises_slack_error_when_no_room(monkeypatch):
    monkeypatch.delenv("HERMES_TALK_ROOM_BOSS", raising=False)
    monkeypatch.delenv("HERMES_TALK_ROOM_SYSTEMS", raising=False)
    with pytest.raises(SlackNotifierError):
        SlackNotifier(channel="C0B6MPN1U3U").post_message(text="x")


# --- category-name routing (the live misroute) -------------------------------

def _talk_rooms(monkeypatch):
    monkeypatch.setenv("HERMES_TALK_ROOM_BOSS", "room-boss")
    monkeypatch.setenv("HERMES_TALK_ROOM_RENEWALS", "room-renew")
    monkeypatch.setenv("HERMES_TALK_ROOM_SYSTEMS", "room-sys")


@pytest.mark.parametrize("category,expected", [
    ("boss", "room-boss"),
    ("renewals", "room-renew"),
    ("systems", "room-sys"),
])
def test_a_category_name_routes_to_its_own_room(monkeypatch, category, expected):
    """Passing the category name is the obvious thing to do, and it used to fall
    through to the boss room because only three hardcoded Slack ids were mapped."""
    from hermes_integrations.team_notify import resolve_room

    _talk_rooms(monkeypatch)
    assert resolve_room(category) == expected


def test_legacy_slack_ids_still_route(monkeypatch):
    """Already-deployed callers pass ids; they must keep working."""
    from hermes_integrations.team_notify import _CHANNEL_CATEGORY, resolve_room

    _talk_rooms(monkeypatch)
    for channel_id, category in _CHANNEL_CATEGORY.items():
        assert resolve_room(channel_id) == {"boss": "room-boss", "renewals": "room-renew",
                                            "systems": "room-sys"}[category]


def test_an_unknown_value_falls_back_to_boss_rather_than_raising(monkeypatch):
    """Losing a report to a routing typo is worse than posting it somewhere visible."""
    from hermes_integrations.team_notify import resolve_room

    _talk_rooms(monkeypatch)
    assert resolve_room("C0-NOT-A-REAL-CHANNEL") == "room-boss"
    assert resolve_room("") == "room-boss"
    assert resolve_room(None) == "room-boss"


def test_the_systems_alert_paths_reach_the_systems_room(monkeypatch):
    """The actual defect: both defaults were Slack ids absent from the map, so
    scheduler alerts and renewal escalations all landed in the boss room."""
    from hermes_integrations.team_notify import resolve_room
    from hermes.scheduler.runner import _systems_check_channel

    _talk_rooms(monkeypatch)
    monkeypatch.delenv("HERMES_SYSTEMS_CHECK_CHANNEL", raising=False)
    monkeypatch.delenv("HERMES_SLACK_FALLBACK_CHANNEL", raising=False)
    assert resolve_room(_systems_check_channel()) == "room-sys"

    import importlib

    from hermes.renewals import config as renewal_config
    importlib.reload(renewal_config)
    assert resolve_room(renewal_config.SLACK_SYSTEMS_CHECK) == "room-sys"


def test_no_systems_default_is_an_unmapped_slack_id():
    """Regression guard: a bare Slack id as a default is how this broke."""
    from hermes_integrations.team_notify import _CATEGORY_ENV, _CHANNEL_CATEGORY
    from hermes.scheduler.runner import _systems_check_channel

    default = _systems_check_channel()
    assert default in _CATEGORY_ENV or default in _CHANNEL_CATEGORY, (
        f"{default!r} routes to the boss room by accident"
    )
