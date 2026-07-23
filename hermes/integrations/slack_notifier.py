"""Proactive Hermes report posting — now routed to Nextcloud Talk.

Slack was retired; this module keeps the historical ``SlackNotifier`` /
``SlackNotifierError`` surface so the ~10 report call sites (revenue sentinel,
commission audit, EOM scorecard, changelog, guardrail alerts, …) don't change.
The real work — channel→room routing and Block Kit→markdown rendering — lives in
``team_notify``. ``channel`` is still a Slack channel id; it's resolved to a Talk
room token. Extra legacy kwargs (bot_token, retry_*, client) are accepted and
ignored.
"""
from __future__ import annotations

from typing import Any

from hermes.integrations.team_notify import TeamNotifier, TeamNotifyError

# Back-compat: #systems-check id, still accepted as a default channel.
DEFAULT_SENTINEL_CHANNEL = "C0B6MPN1U3U"


class SlackNotifierError(Exception):
    """Raised when a proactive report fails to post."""


class SlackNotifier(TeamNotifier):
    def __init__(self, *, channel: str | None = None, **kwargs: Any) -> None:
        super().__init__(channel=channel or DEFAULT_SENTINEL_CHANNEL)

    def post_message(self, *, text: str, blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        try:
            return super().post_message(text=text, blocks=blocks)
        except TeamNotifyError as exc:
            raise SlackNotifierError(str(exc)) from exc
