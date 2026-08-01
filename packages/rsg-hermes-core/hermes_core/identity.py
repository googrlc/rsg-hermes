"""Instance identity — which Hermes is running.

RSG runs a *single* Hermes for both owners (Lamar and Gretchen), who share the
same authority and do the same work. Voice is switched per request via a bundled
persona (see ``load_named_persona`` and ``nl_agent._compose_system_prompt``), not
by running a second container. These knobs remain, all optional, all env-driven:

  HERMES_AGENT_ID      stable id stamped on every write for attribution. Optional
                       now that one instance serves both owners; defaults to
                       "hermes". Set it only if you want per-actor stamping.
  HERMES_PERSONA_FILE  path to a markdown persona that overrides the default
                       system identity/voice used by the conversational agent.
  HERMES_MEMORY_SCOPE  Supermemory container scope (defaults to the agent id).

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


def _read_persona_file(key: str) -> str:
    """Raw text of hermes/personas/{key}.md, or '' if the key is bad/unreadable."""
    import re
    from pathlib import Path

    if not key or not re.fullmatch(r"[a-z0-9_-]+", key):
        return ""
    path = Path(__file__).resolve().parent / "personas" / f"{key}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@functools.lru_cache(maxsize=8)
def load_named_persona(key: str) -> str:
    """Load a bundled persona by key from hermes/personas/{key}.md, or '' if absent.

    Lets the conversational agent switch voice per request (e.g. Lamar's
    owner/revenue persona vs the instance default) without a second instance.

    A persona may inherit another by opening with ``<!-- extends: <key> -->``.
    The parent is emitted first and the child last, so the child's rules win on
    anything the two disagree about. This exists so a specialist desk (Cases)
    can layer on the shared client-context desk (CRM) instead of copy-pasting
    it — one place to fix when the client-lookup rules change. A missing parent
    degrades to just the child; a cycle stops at the repeat.
    """
    import re

    extends_re = re.compile(r"^<!--\s*extends:\s*([a-z0-9_-]+)\s*-->", re.IGNORECASE)

    layers: list[str] = []
    seen: set[str] = set()
    current = key
    while current and current not in seen:
        seen.add(current)
        text = _read_persona_file(current)
        if not text:
            break
        match = extends_re.match(text)
        layers.append(extends_re.sub("", text, count=1).strip() if match else text)
        current = match.group(1).lower() if match else ""

    # Parent-most first, child last.
    return "\n\n".join(reversed(layers)).strip()
