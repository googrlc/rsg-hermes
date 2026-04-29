"""Slack Socket Mode: DMs and @mentions → Hermes dispatcher → thread reply."""

from __future__ import annotations

import os
import re
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from hermes.core.client import EspoClient, EspoClientError
from hermes.core.dispatcher import Dispatcher

_SLACK_MSG_LIMIT = 3500


def _strip_leading_mention(text: str) -> str:
    return re.sub(r"^<@[^>]+>\s*", "", (text or "").strip()).strip()


def _chunk(text: str, limit: int = _SLACK_MSG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        parts.append(rest[:limit])
        rest = rest[limit:]
    return parts


def _is_direct_im(event: dict[str, Any]) -> bool:
    if event.get("channel_type") == "im":
        return True
    ch = event.get("channel")
    return isinstance(ch, str) and ch.startswith("D")


def run_slack_socket(espo: EspoClient | None = None) -> None:
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if not bot_token or not app_token:
        raise RuntimeError(
            "Set SLACK_BOT_TOKEN (Bot User OAuth) and SLACK_APP_TOKEN (App-level) for Socket Mode."
        )

    if espo is None:
        try:
            espo = EspoClient()
        except EspoClientError as e:
            raise RuntimeError(str(e)) from e

    dispatcher = Dispatcher()
    app = App(token=bot_token)

    def _handle_text(text: str, say: Any, thread_ts: str | None) -> None:
        line = _strip_leading_mention(text)
        if not line:
            say("Send a Hermes command after the mention, or DM me directly.", thread_ts=thread_ts)
            return
        result = dispatcher.dispatch(espo, line)
        prefix = "" if result.ok else ":warning: "
        for chunk in _chunk(prefix + result.message):
            say(chunk, thread_ts=thread_ts)

    @app.event("app_mention")
    def on_mention(event: dict[str, Any], say: Any) -> None:
        text = event.get("text") or ""
        thread_ts = event.get("thread_ts") or event.get("ts")
        _handle_text(text, say, thread_ts)

    @app.event("message")
    def on_message(event: dict[str, Any], say: Any) -> None:
        if not _is_direct_im(event):
            return
        if event.get("subtype") in ("message_changed", "message_deleted", "channel_join", "channel_leave"):
            return
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return
        text = event.get("text") or ""
        thread_ts = event.get("thread_ts") or event.get("ts")
        _handle_text(text, say, thread_ts)

    handler = SocketModeHandler(app, app_token)
    handler.start()
