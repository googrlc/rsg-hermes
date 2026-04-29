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

    def __init__(self) -> None:
        from hermes.commands import data_entry, lookup, revenue

        self._routes: list[tuple[re.Pattern[str], Handler]] = [
            (re.compile(r"^\s*add\s+", re.I), data_entry.handle),
            (re.compile(r"^\s*(what|who|find|lookup|search)\b", re.I), lookup.handle),
            (re.compile(r"^\s*(cross-?sell|renewal|revenue|opportunity)\b", re.I), revenue.handle),
        ]

    def dispatch(self, client: EspoClient, line: str) -> DispatchResult:
        text = line.strip()
        if not text:
            return DispatchResult(False, "Empty command.")
        for pattern, handler in self._routes:
            if pattern.search(text):
                return handler(client, text)
        return DispatchResult(
            False,
            "No handler matched. Try: add … | what/find/lookup … | cross-sell/renewals …",
        )
