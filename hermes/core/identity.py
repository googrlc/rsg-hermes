"""Per-instance identity — which Hermes is running.

Two containers run from this single repo, differentiated *only* by env. Nothing
about the code changes between them; these three knobs do:

  HERMES_AGENT_ID      stable id stamped on every write so Lamar's and Gretchen's
                       actions are attributable and never confused
                       (e.g. "hermes-lamar" | "hermes-gretch").
  HERMES_PERSONA_FILE  path to a markdown persona that overrides the default
                       system identity/voice used by the conversational agent.
  HERMES_MEMORY_SCOPE  Supermemory container scope so one instance's memory never
                       bleeds into the other's (defaults to the agent id).

Keeping these in one module means there is exactly one definition of "who am I"
for the whole process.
"""

from __future__ import annotations

import functools
import logging
import os

log = logging.getLogger(__name__)

# Neutral default. Matches the column default used in the agent_id migration, so
# pre-existing rows and an unconfigured container agree. Each real deployment is
# expected to set HERMES_AGENT_ID explicitly (hermes-lamar / hermes-gretch).
DEFAULT_AGENT_ID = "hermes"


def agent_id() -> str:
    """Stable id for the running instance, stamped onto writes."""
    return (os.environ.get("HERMES_AGENT_ID") or DEFAULT_AGENT_ID).strip()


def memory_scope() -> str:
    """Supermemory container scope for this instance.

    Defaults to the agent id so memory is isolated per instance out of the box;
    override with HERMES_MEMORY_SCOPE when several instances should share a scope.
    """
    return (os.environ.get("HERMES_MEMORY_SCOPE") or agent_id()).strip()


def disabled_tools() -> frozenset[str]:
    """Tool names this instance must NOT expose (comma-separated HERMES_DISABLED_TOOLS).

    Gretchen's instance is CRM-scoped — it sets HERMES_DISABLED_TOOLS=web_research
    so the agent never offers public-web business research.
    """
    raw = os.environ.get("HERMES_DISABLED_TOOLS", "")
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


@functools.lru_cache(maxsize=8)
def load_persona(path: str | None = None) -> str:
    """Read the persona markdown for this instance, or '' if none is configured.

    A missing/unreadable file is logged and treated as "no persona" — a bad
    HERMES_PERSONA_FILE must never take the agent down, it just falls back to the
    built-in default identity.
    """
    resolved = (path if path is not None else os.environ.get("HERMES_PERSONA_FILE", "")).strip()
    if not resolved:
        return ""
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError as exc:  # unreadable / missing — degrade to default identity
        log.warning("HERMES_PERSONA_FILE=%s is set but could not be read: %s", resolved, exc)
        return ""
