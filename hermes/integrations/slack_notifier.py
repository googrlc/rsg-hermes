"""Slack posting helpers for proactive Hermes messages."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

DEFAULT_SENTINEL_CHANNEL = "D0B2PJYLGQG"


class SlackNotifierError(Exception):
    """Raised when proactive Slack posting fails after retries."""


class SlackNotifier:
    def __init__(
        self,
        *,
        bot_token: str | None = None,
        channel: str | None = None,
        retry_attempts: int | None = None,
        retry_sleep: float | None = None,
        client: WebClient | None = None,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "").strip()
        self.channel = channel or os.environ.get("HERMES_SENTINEL_SLACK_CHANNEL", DEFAULT_SENTINEL_CHANNEL).strip()
        if not self.bot_token:
            raise SlackNotifierError("SLACK_BOT_TOKEN must be set for proactive Slack messages.")
        self.retry_attempts = retry_attempts if retry_attempts is not None else int(os.environ.get("HERMES_SLACK_RETRIES", "2"))
        self.retry_sleep = retry_sleep if retry_sleep is not None else float(os.environ.get("HERMES_SLACK_RETRY_SLEEP", "1.0"))
        self.client = client or WebClient(token=self.bot_token)

    def post_message(
        self,
        *,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        attempts = max(self.retry_attempts + 1, 1)
        last_error: Exception | None = None
        payload: dict[str, Any] = {"channel": self.channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.chat_postMessage(**payload)
                return dict(response.data)
            except SlackApiError as e:
                last_error = e
                if not self._is_retryable(e) or attempt >= attempts:
                    break
                self._sleep_before_retry()
            except Exception as e:  # pragma: no cover - guard for unexpected SDK/runtime failures
                last_error = e
                if attempt >= attempts:
                    break
                self._sleep_before_retry()
        detail = self._error_detail(last_error)
        raise SlackNotifierError(f"Failed to post sentinel briefing to Slack channel {self.channel}: {detail}")

    def _sleep_before_retry(self) -> None:
        if self.retry_sleep > 0:
            time.sleep(self.retry_sleep)

    @staticmethod
    def _is_retryable(error: SlackApiError) -> bool:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        code = response.get("error") if response is not None else ""
        if status in {429, 500, 502, 503, 504}:
            return True
        return str(code) in {"ratelimited", "internal_error"}

    @staticmethod
    def _error_detail(error: Exception | None) -> str:
        if error is None:
            return "unknown error"
        if isinstance(error, SlackApiError):
            response = getattr(error, "response", None)
            code = response.get("error") if response is not None else "unknown"
            return json.dumps({"error": str(code), "status": getattr(response, "status_code", None)})
        return str(error)

