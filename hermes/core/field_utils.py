"""Utilities for reading record fields resilient to naming and format variance.

Upstream systems are inconsistent about field casing — NowCerts returns the same
field as ``effectiveDate`` or ``EffectiveDate`` depending on the endpoint, and our
own tables use snake_case. These helpers absorb that so callers do not have to.
"""

from __future__ import annotations

from typing import Any


def get_first_available(rec: dict[str, Any], *keys: str) -> Any:
    """Return the first non-empty value found in *rec* under any of *keys*.

    Pass every casing variant a field could arrive under, in preference order, so
    whichever the live payload carries is picked up transparently.

    Returns ``None`` when none of the keys has a non-empty value.
    """
    for key in keys:
        value = rec.get(key)
        if value not in (None, ""):
            return value
    return None


def strip_date(val: Any) -> str | None:
    """Extract the date portion from a datetime string."""
    if not val:
        return None
    s = str(val).strip()
    if "T" in s:
        return s.split("T")[0]
    return s[:10] if len(s) >= 10 else s
