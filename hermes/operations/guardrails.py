"""Guardrail enforcement and logging for Hermes operations."""

from __future__ import annotations

import logging
from typing import Any

from hermes_integrations.supabase_client import SupabaseClient, SupabaseClientError

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
