"""Map natural-language style commands to Hermes command handlers."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


@dataclass
class DispatchResult:
    ok: bool
    message: str
    data: dict[str, Any] | None = None


Handler = Callable[["EspoClient", str], DispatchResult]


class Dispatcher:
    """Order matters: first matching pattern wins."""

    def __init__(self, *, use_openai: bool = False) -> None:
        from hermes.commands import data_entry, lookup, revenue

        self.use_openai = use_openai
        self._routes: list[tuple[re.Pattern[str], Handler]] = [
            (re.compile(r"^\s*add\s+", re.I), data_entry.handle),
            (re.compile(r"\b(total\s+premium|sum\s+premium|premium\s+for)\b", re.I), lookup.handle),
            (re.compile(r"^\s*(what|who|find|lookup|search)\b", re.I), lookup.handle),
            (
                re.compile(r"\b(expir(?:e|ing|y)|renewal[-\s]?audit|renewals?|cross-?sell|revenue|opportunit)", re.I),
                revenue.handle,
            ),
        ]

    def dispatch(self, client: EspoClient, line: str, *, _allow_intent: bool = True) -> DispatchResult:
        text = line.strip()
        if not text:
            return DispatchResult(False, "Empty command.")
        for pattern, handler in self._routes:
            if pattern.search(text):
                return handler(client, text)
        if self.use_openai and _allow_intent:
            from hermes.core.intent_openai import command_from_intent

            command = command_from_intent(text)
            if command and command.strip().lower() != text.lower():
                return self.dispatch(client, command, _allow_intent=False)
        return DispatchResult(
            False,
            "No handler matched. Try: add … | what/find/lookup … | cross-sell/renewals …",
        )
