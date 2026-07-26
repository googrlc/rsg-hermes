"""Override resolution and reconciliation. Pure — no I/O.

An override is a human correction that outranks a synced source until the source
catches up. The whole design turns on one question asked at each sync:

    the source now says X. What does that mean for my override?

    X == override_value   the AMS caught up          -> RETIRE
    X == original_value   nothing changed            -> KEEP
    X == anything else    the AMS moved elsewhere    -> CONFLICT

The third branch is the reason this module exists. Retiring on "the source
changed" would discard a correction the moment a carrier touched an unrelated
field. Holding it and flagging costs a human thirty seconds; getting it wrong
puts a wrong number back on a money surface with no trace.

Comparison is value-based, not identity-based: the source may hand back
``"535.65"`` where the override holds ``535.65``, and a string/float mismatch
must not read as a conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"
STATUS_CONFLICTED = "conflicted"

ACTION_KEEP = "keep"
ACTION_RETIRE = "retire"
ACTION_CONFLICT = "conflict"

RETIRED_AMS_MATCHED = "ams_matched"

# Money tolerance. Carriers and the AMS round differently; a cent of drift is
# not a disagreement worth a human's attention.
MONEY_EPSILON = Decimal("0.01")


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def same_value(a: Any, b: Any) -> bool:
    """Value equality across the type sloppiness of a JSON round-trip.

    ``"535.65"`` == ``535.65`` == ``Decimal("535.6500")``. Numbers compare
    within a cent; everything else compares as trimmed, case-folded text.
    ``None`` equals only ``None`` — an absent value is not zero, and treating it
    as zero is how a missing premium becomes a real one.
    """
    if a is None or b is None:
        return a is None and b is None

    da, db = _as_decimal(a), _as_decimal(b)
    if da is not None and db is not None:
        return abs(da - db) <= MONEY_EPSILON

    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b)

    return str(a).strip().casefold() == str(b).strip().casefold()


@dataclass
class Override:
    """One active correction on one field."""

    entity_type: str
    entity_key: str
    field_name: str
    override_value: Any
    original_value: Any = None
    status: str = STATUS_ACTIVE
    approved_by: str = ""
    reason: str | None = None
    id: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Override":
        return cls(
            id=row.get("id"),
            entity_type=str(row.get("entity_type") or ""),
            entity_key=str(row.get("entity_key") or ""),
            field_name=str(row.get("field_name") or ""),
            override_value=row.get("override_value"),
            original_value=row.get("original_value"),
            status=str(row.get("status") or STATUS_ACTIVE),
            approved_by=str(row.get("approved_by") or ""),
            reason=row.get("reason"),
        )


def resolve(source_value: Any, override: Override | None) -> Any:
    """The value the portal should show and calculate with.

    An override that is not active never changes what is displayed — a retired
    or conflicted correction is history, not truth.
    """
    if override is None or not override.is_active:
        return source_value
    return override.override_value


@dataclass
class Reconciliation:
    action: str
    override: Override
    source_value: Any = None
    reason: str | None = None

    @property
    def retires(self) -> bool:
        return self.action == ACTION_RETIRE

    @property
    def conflicts(self) -> bool:
        return self.action == ACTION_CONFLICT


def reconcile(source_value: Any, override: Override) -> Reconciliation:
    """Decide what a fresh source value means for an active override.

    Never mutates. The caller persists the outcome.
    """
    if not override.is_active:
        return Reconciliation(ACTION_KEEP, override, source_value, "not active")

    if same_value(source_value, override.override_value):
        return Reconciliation(
            ACTION_RETIRE, override, source_value, RETIRED_AMS_MATCHED
        )

    if same_value(source_value, override.original_value):
        return Reconciliation(
            ACTION_KEEP, override, source_value, "source unchanged"
        )

    return Reconciliation(
        ACTION_CONFLICT, override, source_value,
        "source moved to a value matching neither the original nor the override",
    )


def _key(entity_type: str, entity_key: str, field_name: str) -> tuple[str, str, str]:
    return (entity_type, str(entity_key).strip(), field_name)


def index_overrides(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], Override]:
    """(entity_type, entity_key, field) -> active Override."""
    out: dict[tuple[str, str, str], Override] = {}
    for row in rows:
        ov = Override.from_row(row)
        if ov.is_active and ov.entity_key and ov.field_name:
            out[_key(ov.entity_type, ov.entity_key, ov.field_name)] = ov
    return out


def apply_overrides(
    records: list[dict[str, Any]],
    overrides: dict[tuple[str, str, str], Override],
    *,
    entity_type: str,
    key_field: str,
) -> list[dict[str, Any]]:
    """Return *records* with active overrides applied.

    Each touched record gains ``_overridden``: ``{field: original_value}`` — so
    the surface can show what was changed and from what, rather than quietly
    presenting a human's number as the AMS's. Records are copied, not mutated.
    """
    if not overrides:
        return records

    out: list[dict[str, Any]] = []
    for record in records:
        key = str(record.get(key_field) or "").strip()
        if not key:
            out.append(record)
            continue

        applied: dict[str, Any] = {}
        updated = dict(record)
        for field in list(record.keys()):
            ov = overrides.get(_key(entity_type, key, field))
            if ov is None:
                continue
            applied[field] = record.get(field)
            updated[field] = ov.override_value

        # A field may be overridden that the source doesn't carry at all.
        for (etype, ekey, field), ov in overrides.items():
            if etype == entity_type and ekey == key and field not in updated:
                applied[field] = None
                updated[field] = ov.override_value

        if applied:
            updated["_overridden"] = applied
        out.append(updated)
    return out
