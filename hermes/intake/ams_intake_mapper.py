"""Intake → NowCerts insured write, with read-back verification (P4).

Turns a locked ``SubmissionObject`` into a NowCerts ``create_insured`` payload and
**proves the write landed** by reading the record back and comparing field-by-field.
The mapping is data-driven from ``ams_field_map.yaml`` — the one place that decides
which fields have an AMS home — so "correct into the AMS" has a single definition.

Contract (mirrors ``ams/writeback.py`` for corrections, adapted to a create):

    build payload (verified fields only)
      → duplicate search
      → create_insured
      → read back by the new GUID
      → verify field-by-field
      → receipt: VERIFIED | COMMITTED_UNVERIFIED | DUPLICATE_FOUND

Safety rules, unchanged from the writeback path:
- **Verified fields only reach the AMS.** A field whose NowCerts key is not yet
  confirmed (``verified: false``) is surfaced in ``unsupported_fields`` — staged
  and visible, never written, never counted as landed.
- **Read-back is the receipt.** A create that returns 200 but does not read back
  matching is ``COMMITTED_UNVERIFIED``, not success.
- I/O is injected (``create_fn`` / ``read_fn`` / ``dup_search_fn``) so the core is
  testable without the live AMS.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_FIELD_MAP_PATH = Path(__file__).resolve().parent / "ams_field_map.yaml"

# insuredType connector code for a commercial risk; prospect at intake.
_COMMERCIAL_INSURED_TYPE = "0"
_PROSPECT_TYPE = 1


@functools.lru_cache(maxsize=1)
def load_field_map() -> dict[str, Any]:
    """The intake→NowCerts field map (cached)."""
    import yaml

    with open(_FIELD_MAP_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# Normalization — applied before write; the read-back is compared in kind.
# String comparison in verify_readback is case-insensitive, so normalizers stay
# conservative (trim) to match the proven map_to_insured behavior and avoid a
# false mismatch; only the connector codes are real transforms.
# ---------------------------------------------------------------------------
def _trim(v: Any) -> str:
    return str(v).strip()


def _join_comma(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x).strip() for x in v if str(x).strip())
    return str(v).strip()


_NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "trim": _trim,
    "fein": _trim,          # send as-entered; confirm digit-vs-dashed with NowCerts later
    "state_2letter": lambda v: str(v).strip().upper(),
    "zip": _trim,
    "phone": _trim,
    "digits": lambda v: "".join(ch for ch in str(v) if ch.isdigit()),
    "join_comma": _join_comma,
    "money": _trim,
    "date": _trim,
    "entity_code": _trim,   # placeholder until the entity code set is confirmed
}


def _normalize(name: Optional[str], value: Any) -> Any:
    fn = _NORMALIZERS.get(name or "trim", _trim)
    return fn(value)


def _resolve(sub: Any, dotted: str) -> Any:
    """Walk a dotted attribute path on the SubmissionObject; None if any hop is missing."""
    node: Any = sub
    for part in dotted.split("."):
        if node is None:
            return None
        node = getattr(node, part, None)
    return node


def _blank(v: Any) -> bool:
    return v in (None, "") or (isinstance(v, (list, tuple, dict)) and len(v) == 0)


# ---------------------------------------------------------------------------
# Payload  (pure)
# ---------------------------------------------------------------------------
def build_insured_payload(sub: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(payload, unsupported_fields) for create_insured.

    Only ``ams_insured`` entries that are ``verified: true`` and have a present
    value reach the payload. Present-but-unverified fields become
    ``unsupported_fields`` — visible, never written.
    """
    fm = load_field_map()
    payload: dict[str, Any] = {}
    unsupported: list[dict[str, Any]] = []

    for entry in fm.get("insured", []):
        path = entry.get("path")
        # Synthetic, code-derived connector codes.
        if path == "(commercial)":
            payload["insuredType"] = _COMMERCIAL_INSURED_TYPE
            continue
        if path == "(prospect)":
            payload["type"] = _PROSPECT_TYPE
            continue

        value = _resolve(sub, path)
        if _blank(value):
            continue

        nowcerts_field = entry.get("nowcerts_field")
        if not entry.get("verified") or not nowcerts_field:
            unsupported.append({
                "path": path,
                "nowcerts_field": nowcerts_field,
                "reason": "NowCerts field key not confirmed",
            })
            continue

        payload[nowcerts_field] = _normalize(entry.get("normalize"), value)

    return payload, unsupported


def classify_fields(sub: Any) -> dict[str, list[dict[str, Any]]]:
    """Group every present, mapped field by its home — the plan preview.

    Covers all sections of the map (insured/coverage/opportunity/questions), so a
    reviewer can see exactly what lands in the AMS vs the ACORD/pipeline vs is
    unsupported, before anything is written.
    """
    fm = load_field_map()
    out: dict[str, list[dict[str, Any]]] = {}
    for section in ("insured", "coverage", "opportunity", "questions"):
        for entry in fm.get(section, []):
            home = entry.get("home", "unsupported")
            out.setdefault(home, []).append({
                "path": entry.get("path") or entry.get("class"),
                "nowcerts_field": entry.get("nowcerts_field"),
                "verified": bool(entry.get("verified")),
            })
    return out


# ---------------------------------------------------------------------------
# Verify  (pure)
# ---------------------------------------------------------------------------
# Connector codes may not read back identically (NowCerts may echo a label), so
# they are written but not strictly verified.
_UNVERIFIED_ON_READBACK = {"insuredType", "type"}


def verify_readback(sent: dict[str, Any], after: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Did the read-back carry what we sent? Case-insensitive, per field.

    Mirrors ``ams/writeback._verify``. No record back → unverified (the write may
    have landed; the caller reports rather than asserting).
    """
    checkable = [k for k in sent if k not in _UNVERIFIED_ON_READBACK]
    if not after:
        return (False, checkable)
    lowered = {str(k).lower(): v for k, v in after.items()}
    mismatched: list[str] = []
    for key in checkable:
        got = lowered.get(key.lower())
        if str(got or "").strip().casefold() != str(sent[key]).strip().casefold():
            mismatched.append(key)
    return (not mismatched, mismatched)


def _extract_guid(created: Any) -> str | None:
    """Pull the new insured's identifier out of a create response, tolerant of casing."""
    if not isinstance(created, dict):
        return None
    for key in ("insuredDatabaseId", "InsuredDatabaseId", "databaseId", "DatabaseId",
                "id", "Id", "guid", "Guid"):
        v = created.get(key)
        if v not in (None, ""):
            return str(v)
    return None


# ---------------------------------------------------------------------------
# Orchestration — I/O injected, so the create+verify is testable without the AMS.
# ---------------------------------------------------------------------------
STATUS_VERIFIED = "VERIFIED"
STATUS_UNVERIFIED = "COMMITTED_UNVERIFIED"
STATUS_DUPLICATE = "DUPLICATE_FOUND"


def create_and_verify(
    sub: Any,
    *,
    create_fn: Callable[[dict[str, Any]], Any],
    read_fn: Callable[[str], dict[str, Any] | None],
    dup_search_fn: Optional[Callable[[dict[str, Any]], list[dict[str, Any]]]] = None,
) -> dict[str, Any]:
    """Create the insured in NowCerts and prove it landed. Returns a receipt.

    ``create_fn(payload)`` creates and returns the AMS response; ``read_fn(guid)``
    reads the record back; ``dup_search_fn(payload)`` (optional) returns candidate
    duplicates — if any, nothing is created and the receipt is DUPLICATE_FOUND.
    """
    payload, unsupported = build_insured_payload(sub)

    if dup_search_fn:
        dups = dup_search_fn(payload) or []
        if dups:
            return {
                "status": STATUS_DUPLICATE,
                "duplicates": dups,
                "sent": payload,
                "unsupported_fields": unsupported,
            }

    created = create_fn(payload)
    guid = _extract_guid(created)
    after = read_fn(guid) if guid else None
    verified, mismatched = verify_readback(payload, after)

    return {
        "status": STATUS_VERIFIED if verified else STATUS_UNVERIFIED,
        "nowcerts_guid": guid,
        "sent": payload,
        "read_back": after,
        "mismatched": mismatched,
        "unsupported_fields": unsupported,
    }
