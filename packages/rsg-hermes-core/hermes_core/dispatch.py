"""The command-dispatch contract: what a command handler returns.

Split out of ``dispatcher.py`` for the same reason the queue contract was split
out of the renewal executor. Every module under ``hermes/commands/`` returns a
``DispatchResult``, and each of them was importing the *Dispatcher engine* to
get at this four-field dataclass. Since the engine imports those same command
modules to route to them, the type and its consumers formed a cycle around a
dataclass that has no behavior at all.

The type lives in the bottom layer; the engine that consumes it lives in
``hermes/agent/``. Nothing here may import a command or a domain.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class DispatchResult:
    ok: bool
    message: str
    data: dict[str, Any] | None = None


Handler = Callable[[str], DispatchResult]
