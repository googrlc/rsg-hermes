"""Shared carrier-appetite helpers.

The `carrier_appetite` table stores approved states as a text[] (`["GA"]`, or
`["ALL"]` for a nationwide appointment) and class codes as a text[], so neither
can be filtered with a plain PostgREST `eq.` — both need array-aware matching.
This module holds that logic once so the Carrier Hub tool and the `/api/carriers`
endpoint answer "who writes this?" the same way.
"""
from __future__ import annotations

import re
from typing import Any

# Columns that actually exist on carrier_appetite. Kept here so a caller can't
# quietly drift onto a column the table doesn't have — PostgREST rejects the whole
# request when it does, which reads downstream as a 500 rather than a bad query.
APPETITE_COLUMNS = (
    "id,carrier_id,carrier_name,lob,appetite_level,min_premium,max_premium,"
    "states_approved,key_requirements,exclusions,class_codes,notes,"
    "effective_date,active,source,source_document,confidence,updated_by,updated_at"
)


def norm_code(v: Any) -> str:
    """Normalize a class code for comparison — "ISO 91341", "91341" and "91-341"
    all reduce to "91341", so a lookup isn't defeated by how the code was typed."""
    stripped = re.sub(r"\b(ISO|NCCI|SIC|NAICS|CLASS|CODE)\b", "", str(v or "").upper())
    return re.sub(r"[^A-Z0-9]", "", stripped)


def writes_state(row: dict[str, Any], state: str) -> bool:
    """Does this appetite row cover `state`?

    A row scoped `["ALL"]` is a nationwide appointment and covers every state, so
    filtering on the literal state alone would drop exactly the carriers with the
    broadest appetite.
    """
    su = str(state or "").strip().upper()
    if not su:
        return True
    arr = row.get("states_approved") or []
    if not isinstance(arr, list):
        arr = [arr]
    up = [str(x).upper() for x in arr if x]
    return "ALL" in up or any(su == x or su in x or x in su for x in up)


def filter_by_state(rows: list[dict[str, Any]], state: str | None) -> list[dict[str, Any]]:
    """Narrow appetite rows to those covering `state` (no-op when state is blank)."""
    if not (state or "").strip():
        return rows
    return [r for r in rows if writes_state(r, state or "")]


def matches_code(row: dict[str, Any], code: str | None) -> bool:
    """Does this row's class_codes array carry `code`? Blank code matches everything.

    Note this is the row's *own* code list, which most rows leave empty. An empty
    list means "no codes recorded", NOT "writes every class" — so an unmatched row
    is excluded rather than assumed eligible.
    """
    want = norm_code(code)
    if not want:
        return True
    arr = row.get("class_codes") or []
    if not isinstance(arr, list):
        arr = [arr]
    return any(norm_code(c) == want for c in arr)
