"""RSG agent framework — shared lifecycle + agent registry.

All agents subclass :class:`hermes.agents.base.AgentRunner`, which enforces
the shared standards (blast-radius, dry-run gating, audit, escalation,
rollback). The registry maps agent names to runner classes for the CLI:
``hermes agent run <name>``.
"""

from __future__ import annotations

from typing import Callable, Dict

from hermes.agents.base import AgentAction, AgentRunner, AgentRunResult, EscalationError, NullNotifier

# Lazy registry: name -> callable(*args, **kwargs) -> AgentRunner.
# Populated by ``register_agent``; agents register themselves on import.
_REGISTRY: Dict[str, Callable[..., AgentRunner]] = {}


def register_agent(name: str) -> Callable:
    """Decorator: register an AgentRunner subclass under ``name``."""

    def _wrap(cls: type[AgentRunner]) -> type[AgentRunner]:
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return _wrap


def available_agents() -> list[str]:
    """Load built-in agents and return their registered names."""
    _load_builtin_agents()
    return sorted(_REGISTRY)


def get_agent_class(name: str) -> type[AgentRunner] | None:
    _load_builtin_agents()
    return _REGISTRY.get(name)


def _load_builtin_agents() -> None:
    # Import side effects register agents via the @register_agent decorator.
    try:
        import hermes.agents.book_hygiene  # noqa: F401
    except Exception as exc:  # optional dep missing must not break the registry
        from hermes.agents.base import log

        log.debug("book_hygiene agent not loadable: %s", exc)


__all__ = [
    "AgentAction",
    "AgentRunner",
    "AgentRunResult",
    "EscalationError",
    "NullNotifier",
    "available_agents",
    "get_agent_class",
    "register_agent",
]
