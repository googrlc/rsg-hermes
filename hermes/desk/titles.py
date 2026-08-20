"""Standardized Desk case titles (CF-02)."""

from __future__ import annotations

import re

from hermes.desk.spec import CATEGORY_SHORT

_WHITESPACE = re.compile(r"\s+")


def _clean(value: str | None, *, fallback: str) -> str:
    text = _WHITESPACE.sub(" ", (value or "").strip())
    return text or fallback


def case_title(
    category: str | None,
    client: str | None,
    policy_number: str | None,
    short_request: str | None,
) -> str:
    """``[Category] | [Client] | [Policy Number] | [Short Request]``.

    Example: ``Certificate | ABC Trucking LLC | CA123456 | Holder request``.
    """
    label = CATEGORY_SHORT.get(category or "", None) or _clean(category, fallback="Service")
    return " | ".join(
        (
            label,
            _clean(client, fallback="Unknown client"),
            _clean(policy_number, fallback="No policy"),
            _clean(short_request, fallback="Request"),
        )
    )
