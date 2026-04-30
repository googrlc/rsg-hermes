"""Slack Socket Mode: DMs and @mentions → Hermes dispatcher → thread reply."""

from __future__ import annotations

import os
import re
import logging
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from hermes.core.client import EspoClient, EspoClientError
from hermes.core.dispatcher import Dispatcher

_SLACK_MSG_LIMIT = 3500
log = logging.getLogger(__name__)


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

    use_openai = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("HERMES_OPENAI_API_KEY"))
    dispatcher = Dispatcher(use_openai=use_openai)
    app = App(token=bot_token)

    def _handle_text(
        text: str,
        say: Any,
        thread_ts: str | None,
        *,
        channel_id: str | None = None,
        user_id: str | None = None,
        message_ts: str | None = None,
    ) -> None:
        line = _strip_leading_mention(text)
        if not line:
            say("Send a Hermes command after the mention, or DM me directly.", thread_ts=thread_ts)
            return
        dispatcher.set_slack_context(
            channel_id=channel_id,
            user_id=user_id,
            message_ts=message_ts,
        )
        result = dispatcher.dispatch(espo, line)
        prefix = "" if result.ok else ":warning: "
        for chunk in _chunk(prefix + result.message):
            say(chunk, thread_ts=thread_ts)

    @app.event("app_mention")
    def on_mention(event: dict[str, Any], say: Any) -> None:
        text = event.get("text") or ""
        thread_ts = event.get("thread_ts") or event.get("ts")
        _handle_text(
            text, say, thread_ts,
            channel_id=event.get("channel"),
            user_id=event.get("user"),
            message_ts=event.get("ts"),
        )

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
        files = event.get("files") or []
        pdfs = [
            f
            for f in files
            if isinstance(f, dict)
            and (
                f.get("mimetype") == "application/pdf"
                or str(f.get("name") or "").lower().endswith(".pdf")
            )
        ]
        if pdfs:
            log.info("pdf_folder_ingest placeholder received %s PDF(s)", len(pdfs))
            say("PDF received. The pdf_folder_ingest pipeline is queued as a follow-up placeholder.", thread_ts=thread_ts)
        if text.strip():
            _handle_text(
                text, say, thread_ts,
                channel_id=event.get("channel"),
                user_id=event.get("user"),
                message_ts=event.get("ts"),
            )

    handler = SocketModeHandler(app, app_token)
    handler.start()
