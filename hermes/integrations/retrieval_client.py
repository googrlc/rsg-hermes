"""Supabase helpers for the agency-memory retrieval tables.

Thin wrappers over `SupabaseClient` for the eight retrieval tables introduced
in the `20260520063000_agency_memory_retrieval_tables.sql` migration.

Tables:
  - client_entities       canonical retrieval index for Accounts/Contacts/Opps
  - client_relationships  person↔entity links
  - client_facts          structured key/value facts (EIN, phone, DOB, …)
  - client_notes          structured narrative notes
  - client_documents      document references with summary
  - quote_facts           per-quote financial detail
  - policy_facts          per-policy detail
  - underwriting_facts    risk/exposure facts that drive carrier appetite

Consumed by `hermes.commands.agency_intake` and
`hermes.operations.agency_intake_approval`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

EntityType = str  # Account | Contact | Household | Opportunity | Policy | Renewal | Lead
Sensitivity = str  # standard | restricted
Confidence = str  # low | medium | high

_PHONE_DIGITS = re.compile(r"\D+")


def _normalize_value(label: str, value: str) -> str | None:
    """Build a normalized form for matching (phone digits, lowercased email)."""
    if value is None:
        return None
    label_low = label.lower()
    if "email" in label_low:
        return value.strip().lower()
    if "phone" in label_low:
        digits = _PHONE_DIGITS.sub("", value)
        return digits or None
    if label_low in {"ein", "fein"}:
        return _PHONE_DIGITS.sub("", value) or None
    return None


def upsert_entity(
    supa: SupabaseClient,
    *,
    entity_type: EntityType,
    entity_name: str,
    crm_account_id: str | None = None,
    crm_contact_id: str | None = None,
    crm_opportunity_id: str | None = None,
    crm_policy_id: str | None = None,
    crm_renewal_id: str | None = None,
    crm_lead_id: str | None = None,
    canonical_aliases: list[str] | None = None,
    primary_account_entity_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Insert a `client_entities` row.

    Caller should search first via `search_entities` to avoid duplicates;
    this is a plain insert, not an upsert by name (the table allows
    multiple Accounts with the same name when CRM ids differ).
    """
    payload = {
        "entity_type": entity_type,
        "entity_name": entity_name,
        "crm_account_id": crm_account_id,
        "crm_contact_id": crm_contact_id,
        "crm_opportunity_id": crm_opportunity_id,
        "crm_policy_id": crm_policy_id,
        "crm_renewal_id": crm_renewal_id,
        "crm_lead_id": crm_lead_id,
        "canonical_aliases": canonical_aliases or [entity_name],
        "primary_account_entity_id": primary_account_entity_id,
        "tags": tags or [],
    }
    return supa.insert("client_entities", payload)


def search_entities(
    supa: SupabaseClient,
    *,
    entity_type: EntityType | None = None,
    name: str | None = None,
    crm_account_id: str | None = None,
    crm_contact_id: str | None = None,
    crm_opportunity_id: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Look up entities by CRM id (preferred) or name (case-insensitive substring)."""
    params: dict[str, str] = {}
    if entity_type:
        params["entity_type"] = f"eq.{entity_type}"
    if crm_account_id:
        params["crm_account_id"] = f"eq.{crm_account_id}"
    if crm_contact_id:
        params["crm_contact_id"] = f"eq.{crm_contact_id}"
    if crm_opportunity_id:
        params["crm_opportunity_id"] = f"eq.{crm_opportunity_id}"
    if name:
        params["entity_name"] = f"ilike.%{name}%"
    return supa.select("client_entities", params=params, limit=limit)


def insert_fact(
    supa: SupabaseClient,
    *,
    entity_id: str,
    fact_label: str,
    fact_value: str,
    source: str,
    sensitivity: Sensitivity = "standard",
    confidence: Confidence = "high",
    source_date: str | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    """Insert one `client_facts` row, computing the normalized value when possible."""
    payload: dict[str, Any] = {
        "entity_id": entity_id,
        "fact_label": fact_label,
        "fact_value": fact_value,
        "fact_value_normalized": _normalize_value(fact_label, fact_value),
        "sensitivity": sensitivity,
        "confidence": confidence,
        "source": source,
    }
    if source_date:
        payload["source_date"] = source_date
    if source_ref:
        payload["source_ref"] = source_ref
    return supa.insert("client_facts", payload)


def search_facts(
    supa: SupabaseClient,
    *,
    entity_id: str | None = None,
    fact_label: str | None = None,
    fact_value: str | None = None,
    include_restricted: bool = False,
    include_superseded: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Look up facts by entity / label / value. Restricted facts are opt-in."""
    params: dict[str, str] = {}
    if entity_id:
        params["entity_id"] = f"eq.{entity_id}"
    if fact_label:
        params["fact_label"] = f"eq.{fact_label}"
    if fact_value:
        normalized = _normalize_value(fact_label or "", fact_value) or fact_value
        params["fact_value_normalized"] = f"eq.{normalized}"
    if not include_restricted:
        params["sensitivity"] = "eq.standard"
    if not include_superseded:
        params["superseded_at"] = "is.null"
    return supa.select("client_facts", params=params, limit=limit)


def insert_note(
    supa: SupabaseClient,
    *,
    entity_id: str,
    note_type: str,
    title: str,
    summary: str,
    full_text: str | None = None,
    audience: str = "internal",
    sensitivity: Sensitivity = "standard",
    tags: list[str] | None = None,
    author: str | None = None,
    note_date: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    crm_account_id: str | None = None,
    crm_contact_id: str | None = None,
    crm_opportunity_id: str | None = None,
) -> dict[str, Any]:
    """Insert one `client_notes` row paired with the agency CRM note."""
    payload: dict[str, Any] = {
        "entity_id": entity_id,
        "note_type": note_type,
        "title": title,
        "summary": summary,
        "full_text": full_text,
        "audience": audience,
        "sensitivity": sensitivity,
        "tags": tags or [],
        "author": author,
        "note_date": note_date,
        "source": source,
        "source_ref": source_ref,
        "crm_account_id": crm_account_id,
        "crm_contact_id": crm_contact_id,
        "crm_opportunity_id": crm_opportunity_id,
    }
    return supa.insert("client_notes", payload)


def insert_quote_fact(
    supa: SupabaseClient,
    *,
    entity_id: str,
    quote_number: str,
    line_of_business: str,
    carrier: str | None = None,
    premium: float | None = None,
    fees: float | None = None,
    taxes: float | None = None,
    total: float | None = None,
    effective_date: str | None = None,
    expiration_date: str | None = None,
    status: str | None = None,
    coverage_limits: dict[str, Any] | None = None,
    deductibles: dict[str, Any] | None = None,
    endorsements: list[Any] | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    crm_opportunity_id: str | None = None,
) -> dict[str, Any]:
    """Insert one `quote_facts` row (upsert on quote_number + line_of_business)."""
    payload: dict[str, Any] = {
        "entity_id": entity_id,
        "quote_number": quote_number,
        "line_of_business": line_of_business,
        "carrier": carrier,
        "premium": premium,
        "fees": fees,
        "taxes": taxes,
        "total": total,
        "effective_date": effective_date,
        "expiration_date": expiration_date,
        "status": status,
        "coverage_limits": coverage_limits,
        "deductibles": deductibles,
        "endorsements": endorsements,
        "source": source,
        "source_ref": source_ref,
        "crm_opportunity_id": crm_opportunity_id,
    }
    return supa.upsert("quote_facts", payload, on_conflict="quote_number,line_of_business")


def bulk_insert_facts(
    supa: SupabaseClient,
    *,
    facts: Iterable[dict[str, Any]],
    entity_id_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    """Insert many `client_facts` rows.

    `facts` items must have at least: entity (display name), fact_label,
    fact_value, source. `entity_id_lookup` maps entity name → client_entities.id
    so the caller can stage rows before all CRM ids are known.
    """
    inserted: list[dict[str, Any]] = []
    for fact in facts:
        entity_name = fact.get("entity")
        if not entity_name:
            log.warning("Skipping fact without entity name: %s", fact)
            continue
        entity_id = entity_id_lookup.get(entity_name)
        if not entity_id:
            log.warning("No entity id for fact entity=%s", entity_name)
            continue
        row = insert_fact(
            supa,
            entity_id=entity_id,
            fact_label=fact.get("fact_label") or "",
            fact_value=str(fact.get("fact_value") or ""),
            source=str(fact.get("source") or "agency-intake"),
            sensitivity=fact.get("sensitivity") or "standard",
            confidence=fact.get("confidence") or "high",
            source_date=fact.get("source_date"),
            source_ref=fact.get("source_ref"),
        )
        inserted.append(row)
    return inserted
