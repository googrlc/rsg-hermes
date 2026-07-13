"""Live-metadata payload conforming and name normalization for outbound sync.

Two long-standing outbound failure modes are addressed here:

1. **camelCase vs. raw-internal drift.** ``map_insured_to_account`` emits a mix
   of EspoCRM camelCase fields and raw NowCerts/internal snake_case keys
   (``momentum_client_id``, ``account_type``, ``years_in_business``,
   ``momentum_last_synced``). EspoCRM rejects a write that references an unknown
   field (400 ``validationFailure``) or silently drops a mis-cased one. Before a
   payload is enqueued we conform it against the *live* Account metadata: keep
   fields that exist, remap an unambiguous snake/camel variant that exists, and
   drop the rest (logging each dropped field once) rather than failing the row.

2. **Missed dedup → blind create → 409.** The dedup search attribute
   (``momentum_client_id``) must match the actual Espo field name. The same
   name-resolution used for conforming is exposed via ``resolve_field_name`` so
   the mapping resolver searches on the attribute Espo really has.

Metadata is fetched through ``EspoClient.get_metadata`` (in-memory TTL cache),
so repeated conforms in one run cost a single API call. If metadata cannot be
read the functions **fail open** (return the payload unchanged) — never strip a
whole payload just because the metadata endpoint hiccuped.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from hermes.core.client import EspoClient, EspoClientError

log = logging.getLogger(__name__)

# (entity, field) pairs already logged as dropped/remapped, so a daily run emits
# one line per offending field instead of one per row.
_logged_drops: set[tuple[str, str]] = set()
_logged_remaps: set[tuple[str, str, str]] = set()

# Common US corporate suffixes stripped before normalized-name comparison.
_CORP_SUFFIXES = {
    "llc", "l.l.c", "inc", "incorporated", "corp", "corporation", "co",
    "company", "ltd", "limited", "lp", "llp", "pllc", "pc", "pa", "group",
    "holdings", "enterprises", "services", "and", "the",
}


def snake_to_camel(name: str) -> str:
    parts = name.split("_")
    if len(parts) == 1:
        return name
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:] if p)


def camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def normalize_name(name: str | None) -> str:
    """Normalize an account name for dedup comparison.

    Lowercases, strips punctuation and common corporate suffixes, and collapses
    whitespace so "Shamira Douglas, LLC" and "shamira douglas llc" compare equal.
    Returns "" for empty input.
    """
    if not name:
        return ""
    s = str(name).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in _CORP_SUFFIXES]
    return " ".join(tokens)


def _entity_field_names(espo: EspoClient, entity: str) -> set[str]:
    """Return the set of attribute names EspoCRM accepts for ``entity``.

    Includes scalar/field names plus ``<link>Id`` / ``<link>Name`` for each
    relationship (EspoCRM accepts those on write). Returns an empty set if
    metadata is unavailable — callers treat that as "unknown, keep everything".
    """
    try:
        defs = espo.get_metadata("entityDefs")
    except EspoClientError as exc:
        log.warning("Could not load Espo entityDefs for %s conform: %s", entity, exc)
        return set()
    if not isinstance(defs, dict):
        return set()
    entity_def = defs.get(entity)
    if not isinstance(entity_def, dict):
        return set()

    names: set[str] = set()
    fields = entity_def.get("fields")
    if isinstance(fields, dict):
        names.update(fields.keys())
    links = entity_def.get("links")
    if isinstance(links, dict):
        for link_name in links:
            names.add(f"{link_name}Id")
            names.add(f"{link_name}Name")
    names.add("id")
    return names


def resolve_field_name(espo: EspoClient, entity: str, candidate: str) -> str | None:
    """Return the real Espo attribute matching ``candidate``, or None.

    Matches exactly first, then tries the snake↔camel variant. Used so the
    dedup search runs against the attribute Espo actually exposes.
    """
    fields = _entity_field_names(espo, entity)
    if not fields:
        # Metadata unavailable — assume the caller's candidate is correct.
        return candidate
    if candidate in fields:
        return candidate
    for variant in (snake_to_camel(candidate), camel_to_snake(candidate)):
        if variant != candidate and variant in fields:
            return variant
    return None


def conform_payload_to_metadata(
    espo: EspoClient, entity: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Drop or remap payload fields the live Espo ``entity`` does not accept.

    - Field present in metadata → kept as-is.
    - Field absent but an unambiguous snake/camel variant is present → remapped.
    - Otherwise → dropped, logged once per (entity, field).

    Never raises on unknown fields; a bad field costs the field, not the row.
    Fails open (returns the payload unchanged) when metadata is unavailable.
    """
    fields = _entity_field_names(espo, entity)
    if not fields:
        return dict(payload)

    conformed: dict[str, Any] = {}
    for key, value in payload.items():
        if key in fields:
            conformed[key] = value
            continue
        remapped = None
        for variant in (snake_to_camel(key), camel_to_snake(key)):
            if variant != key and variant in fields:
                remapped = variant
                break
        if remapped:
            conformed[remapped] = value
            sig = (entity, key, remapped)
            if sig not in _logged_remaps:
                _logged_remaps.add(sig)
                log.info("Conform %s: remapped field %r -> %r", entity, key, remapped)
        else:
            sig = (entity, key)
            if sig not in _logged_drops:
                _logged_drops.add(sig)
                log.warning(
                    "Conform %s: dropped unknown field %r (not in live Espo metadata)",
                    entity, key,
                )
    return conformed
