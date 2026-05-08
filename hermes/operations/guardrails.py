"""Guardrail enforcement and logging for Hermes operations."""

from __future__ import annotations

import logging
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

VALID_SEVERITIES = ("LOW", "INFO", "MEDIUM", "HIGH", "CRITICAL")


class GuardrailViolation(Exception):
    """Raised when a Hermes operation is blocked by a guardrail."""


def log_guardrail_event(
    supa: SupabaseClient,
    *,
    agent_role: str,
    attempted_action: str,
    rule_violated: str,
    context_payload: dict[str, Any] | None = None,
    severity: str = "MEDIUM",
) -> dict[str, Any]:
    """Record a guardrail violation to ``guardrail_logs``."""
    if severity not in VALID_SEVERITIES:
        severity = "MEDIUM"
    payload = {
        "agent_role": agent_role,
        "attempted_action": attempted_action,
        "rule_violated": rule_violated,
        "context_payload": context_payload or {},
        "severity": severity,
    }
    try:
        row = supa.insert("guardrail_logs", payload)
        log.info(
            "Guardrail logged: role=%s action=%s rule=%s severity=%s",
            agent_role,
            attempted_action,
            rule_violated,
            severity,
        )
        return row
    except SupabaseClientError:
        log.exception("Failed to write guardrail log to Supabase")
        raise


def validate_slack_channel(
    supa: SupabaseClient,
    *,
    channel_id: str,
    agent_role: str,
) -> dict[str, Any]:
    """Verify a Slack channel is registered, active, and allows the given role.

    Returns the registry row on success.
    Raises ``GuardrailViolation`` on any mismatch and logs to ``guardrail_logs``.
    """
    rows = supa.select(
        "slack_registry",
        params={"channel_id": f"eq.{channel_id}"},
        limit=1,
    )
    if not rows:
        log_guardrail_event(
            supa,
            agent_role=agent_role,
            attempted_action="slack.post_message",
            rule_violated="slack_registry_miss",
            context_payload={"requested_channel_id": channel_id},
            severity="HIGH",
        )
        raise GuardrailViolation(
            f"Channel {channel_id} not found in slack_registry"
        )

    entry = rows[0]
    if not entry.get("is_active"):
        log_guardrail_event(
            supa,
            agent_role=agent_role,
            attempted_action="slack.post_message",
            rule_violated="slack_channel_inactive",
            context_payload={
                "channel_id": channel_id,
                "channel_name": entry.get("channel_name"),
            },
            severity="MEDIUM",
        )
        raise GuardrailViolation(
            f"Channel {channel_id} ({entry.get('channel_name')}) is inactive"
        )

    allowed_roles = entry.get("allowed_ai_roles") or []
    if agent_role not in allowed_roles:
        log_guardrail_event(
            supa,
            agent_role=agent_role,
            attempted_action="slack.post_message",
            rule_violated="slack_role_not_allowed",
            context_payload={
                "channel_id": channel_id,
                "channel_name": entry.get("channel_name"),
                "agent_role": agent_role,
                "allowed_roles": allowed_roles,
            },
            severity="MEDIUM",
        )
        raise GuardrailViolation(
            f"Role {agent_role} not allowed on channel "
            f"{channel_id} ({entry.get('channel_name')})"
        )

    return entry
