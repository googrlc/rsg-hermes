"""Utilities for reading CRM record fields resilient to snake_case / camelCase variants.

EspoCRM is mid-transition between camelCase and snake_case field naming. Use
``get_first_available`` whenever a field could appear under either name.
"""

from __future__ import annotations

from typing import Any


def get_first_available(rec: dict[str, Any], *keys: str) -> Any:
    """Return the first non-empty value found in *rec* under any of *keys*.

    Handles the EspoCRM camelCase ↔ snake_case transition: pass the snake_case
    variant first and the camelCase variant second (or vice-versa) so whichever
    the live schema returns is picked up transparently.

    Returns ``None`` when none of the keys has a non-empty value.
    """
    for key in keys:
        value = rec.get(key)
        if value not in (None, ""):
            return value
    return None
