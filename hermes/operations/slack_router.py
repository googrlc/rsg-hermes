"""Slack routing layer that enforces the ``slack_registry`` guardrail."""

from __future__ import annotations

import logging
from typing import Any

from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient
from hermes.operations.guardrails import GuardrailViolation, validate_slack_channel

log = logging.getLogger(__name__)


class RegistryAwareSlackRouter:
    """Posts to Slack only via channels validated against ``slack_registry``."""

    def __init__(
        self,
        supa: SupabaseClient,
        notifier: SlackNotifier | None = None,
    ) -> None:
        self.supa = supa
        self._notifier = notifier

    def _get_notifier(self, channel_id: str) -> SlackNotifier:
        if self._notifier:
            if self._notifier.channel != channel_id:
                raise ValueError(
                    f"Injected notifier targets channel {self._notifier.channel} "
                    f"but registry validated {channel_id}"
                )
            return self._notifier
        return SlackNotifier(channel=channel_id)

    def post(
        self,
        *,
        channel_id: str,
        agent_role: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Post a message after validating the channel against the registry.

        Raises ``GuardrailViolation`` if the channel is missing, inactive,
        or the role is not allowed.
        """
        entry = validate_slack_channel(
            self.supa,
            channel_id=channel_id,
            agent_role=agent_role,
        )
        log.info(
            "Slack routing approved: channel=%s (%s) role=%s",
            channel_id,
            entry.get("channel_name"),
            agent_role,
        )
        notifier = self._get_notifier(channel_id)
        return notifier.post_message(text=text, blocks=blocks)

    def resolve_channel_for_role(self, agent_role: str) -> list[dict[str, Any]]:
        """Return all active channels that allow the given role."""
        rows = self.supa.select(
            "slack_registry",
            params={"is_active": "eq.true"},
            limit=50,
        )
        return [
            row
            for row in rows
            if agent_role in (row.get("allowed_ai_roles") or [])
        ]
