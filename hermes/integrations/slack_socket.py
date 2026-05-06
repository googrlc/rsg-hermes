"""Slack Socket Mode: DMs and @mentions → Hermes dispatcher → thread reply."""

from __future__ import annotations

import os
import re
import logging
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

from hermes.core.client import EspoClient, EspoClientError
from hermes.core.dispatcher import Dispatcher, DispatchResult
from hermes.jobs import commission_reconciliation
from hermes.jobs import revenue_sentinel
from hermes.jobs import revenue_integrity

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


def _is_command_channel(event: dict[str, Any], command_channel: str | None) -> bool:
    if not command_channel:
        return False
    return event.get("channel") == command_channel


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
    fallback_channel = os.environ.get("HERMES_SLACK_FALLBACK_CHANNEL", "C0AFHN83ZE3")
    command_channel = os.environ.get("HERMES_SLACK_COMMAND_CHANNEL") or fallback_channel

    def _send_reply(
        *,
        say: Any,
        channel_id: str | None,
        user_id: str | None,
        text: str,
        thread_ts: str | None,
    ) -> None:
        if channel_id:
            try:
                app.client.chat_postMessage(
                    channel=channel_id,
                    text=text,
                    thread_ts=thread_ts,
                )
                return
            except SlackApiError as e:
                error = e.response.get("error") if getattr(e, "response", None) else "unknown"
                log.exception("Slack chat_postMessage failed channel=%s user=%s error=%s", channel_id, user_id, error)
                if user_id:
                    try:
                        dm = app.client.conversations_open(users=user_id)
                        dm_channel = (dm.get("channel") or {}).get("id")
                        if dm_channel:
                            app.client.chat_postMessage(
                                channel=dm_channel,
                                text=text,
                            )
                            return
                    except SlackApiError as dm_error:
                        dm_code = dm_error.response.get("error") if getattr(dm_error, "response", None) else "unknown"
                        log.exception("Slack DM fallback failed user=%s error=%s", user_id, dm_code)
                if fallback_channel:
                    try:
                        app.client.chat_postMessage(
                            channel=fallback_channel,
                            text=f"Hermes could not reply in channel `{channel_id}` for <@{user_id}>.\n\n{text}",
                        )
                        return
                    except SlackApiError as fallback_error:
                        fallback_code = (
                            fallback_error.response.get("error")
                            if getattr(fallback_error, "response", None)
                            else "unknown"
                        )
                        log.exception("Slack fallback channel failed channel=%s error=%s", fallback_channel, fallback_code)
        try:
            say(text, thread_ts=thread_ts)
        except SlackApiError as e:
            error = e.response.get("error") if getattr(e, "response", None) else "unknown"
            log.exception("Slack say failed channel=%s user=%s error=%s", channel_id, user_id, error)

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
            _send_reply(
                say=say,
                channel_id=channel_id,
                user_id=user_id,
                text="Send a Hermes command after the mention, or DM me directly.",
                thread_ts=thread_ts,
            )
            return
        dispatcher.set_slack_context(
            channel_id=channel_id,
            user_id=user_id,
            message_ts=message_ts,
        )
        try:
            result = dispatcher.dispatch(espo, line)
        except EspoClientError as e:
            log.exception("Hermes CRM command failed")
            result = DispatchResult(False, f"CRM command failed: {e}")
        except Exception as e:
            log.exception("Hermes command failed")
            result = DispatchResult(False, f"Hermes command failed: {e}")
        prefix = "" if result.ok else ":warning: "
        for chunk in _chunk(prefix + result.message):
            _send_reply(
                say=say,
                channel_id=channel_id,
                user_id=user_id,
                text=chunk,
                thread_ts=thread_ts,
            )

    @app.event("app_mention")
    def on_mention(event: dict[str, Any], say: Any) -> None:
        log.info("app_mention received: channel=%s user=%s", event.get("channel"), event.get("user"))
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
        log.info("message event: channel=%s type=%s is_dm=%s is_cmd=%s", event.get("channel"), event.get("channel_type"), _is_direct_im(event), _is_command_channel(event, command_channel))
        if not _is_direct_im(event) and not _is_command_channel(event, command_channel):
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
            _send_reply(
                say=say,
                channel_id=event.get("channel"),
                user_id=event.get("user"),
                text="PDF received. The pdf_folder_ingest pipeline is queued as a follow-up placeholder.",
                thread_ts=thread_ts,
            )
        if text.strip():
            _handle_text(
                text, say, thread_ts,
                channel_id=event.get("channel"),
                user_id=event.get("user"),
                message_ts=event.get("ts"),
            )

    @app.action(re.compile(r"^sentinel_"))
    def on_sentinel_action(ack: Any, body: dict[str, Any], action: dict[str, Any], respond: Any) -> None:
        ack()
        action_id = str(action.get("action_id") or "")
        value = str(action.get("value") or "")
        user_id = ((body.get("user") or {}).get("id") if isinstance(body, dict) else None) or "unknown"
        try:
            result = revenue_sentinel.handle_slack_action(
                client=espo,
                action=action_id,
                action_value=value,
            )
        except (ValueError, EspoClientError) as e:
            log.exception("Sentinel action failed user=%s action=%s", user_id, action_id)
            result = f"Sentinel action failed: {e}"
        respond(
            text=result,
            response_type="ephemeral",
            replace_original=False,
        )

    @app.action(re.compile(r"^commission_"))
    def on_commission_action(ack: Any, body: dict[str, Any], action: dict[str, Any], respond: Any) -> None:
        ack()
        action_id = str(action.get("action_id") or "")
        value = str(action.get("value") or "")
        user_id = ((body.get("user") or {}).get("id") if isinstance(body, dict) else None) or "unknown"
        try:
            if action_id == "commission_create_dispute":
                result = commission_reconciliation.handle_dispute_action(
                    client=espo,
                    action=action_id,
                    action_value=value,
                )
            else:
                result = revenue_integrity.handle_commission_action(
                    client=espo,
                    action=action_id,
                    action_value=value,
                )
        except (ValueError, EspoClientError) as e:
            log.exception("Commission action failed user=%s action=%s", user_id, action_id)
            result = f"Commission action failed: {e}"
        respond(
            text=result,
            response_type="ephemeral",
            replace_original=False,
        )

    handler = SocketModeHandler(app, app_token)
    handler.start()
