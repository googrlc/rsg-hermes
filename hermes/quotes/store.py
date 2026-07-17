"""Opportunity quotes — CRUD for carrier quotes attached to a pipeline opportunity.

A quote is a first-class record: one opportunity can hold several carrier quotes,
each with its own terms and its own PDF (filed into the client's Nextcloud
``Quotes/`` folder). This module owns the ``opportunity_quotes`` table; the
approval-gated NowCerts write-back lives in ``hermes/quotes/executor.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

TABLE = "opportunity_quotes"

# Draft -> (Queued -> Sent) via the AMS write-back; Bound/Lost are outcomes.
STATUS_DRAFT = "Draft"
STATUS_QUEUED = "Queued"
STATUS_SENT = "Sent"
STATUS_BOUND = "Bound"
STATUS_LOST = "Lost"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_date(value: Any) -> str | None:
    """Accept a YYYY-MM-DD string (or empty) — Postgres validates the rest."""
    s = str(value or "").strip()
    return s or None


def create_quote(
    supa: "SupabaseClient",
    *,
    opportunity: dict[str, Any],
    carrier: str | None = None,
    line_of_business: str | None = None,
    premium: Any = None,
    effective_date: Any = None,
    expiration_date: Any = None,
    quote_number: str | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a quote row under *opportunity*, denormalizing its client linkage."""
    oid = str(opportunity.get("id") or "")
    if not oid:
        raise ValueError("opportunity id is required to create a quote")
    row = supa.insert(
        TABLE,
        {
            "opportunity_id": oid,
            "client_identifier": opportunity.get("client_identifier"),
            "insured_id": opportunity.get("insured_id"),
            "insured_name": opportunity.get("insured_name"),
            "line_of_business": line_of_business or opportunity.get("line_of_business"),
            "carrier": (carrier or opportunity.get("carrier") or None),
            "premium": _clean_num(premium),
            "effective_date": _clean_date(effective_date),
            "expiration_date": _clean_date(expiration_date),
            "quote_number": (quote_number or None),
            "notes": (notes or None),
            "status": STATUS_DRAFT,
            "created_by": created_by,
        },
    )
    return row


def attach_document(
    supa: "SupabaseClient",
    quote_id: str,
    *,
    url: str,
    path: str,
    filename: str,
) -> dict[str, Any]:
    """Stamp the filed Nextcloud PDF onto a quote row."""
    return supa.update(
        TABLE,
        quote_id,
        {
            "document_url": url,
            "document_path": path,
            "document_filename": filename,
            "updated_at": _utcnow_iso(),
        },
    )


def set_status(supa: "SupabaseClient", quote_id: str, status: str) -> dict[str, Any]:
    return supa.update(TABLE, quote_id, {"status": status, "updated_at": _utcnow_iso()})


def link_nowcerts(
    supa: "SupabaseClient",
    quote_id: str,
    *,
    quote_number: str | None = None,
    nowcerts_quote_guid: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Backfill NowCerts identifiers onto a quote after the executor writes it."""
    payload: dict[str, Any] = {
        k: v
        for k, v in {
            "quote_number": quote_number,
            "nowcerts_quote_guid": nowcerts_quote_guid,
            "status": status,
        }.items()
        if v is not None
    }
    if not payload:
        return {}
    payload["updated_at"] = _utcnow_iso()
    return supa.update(TABLE, quote_id, payload)


def get_quote(supa: "SupabaseClient", quote_id: str) -> dict[str, Any] | None:
    rows = supa.select(TABLE, columns="*", params={"id": f"eq.{quote_id}"}, limit=1)
    return rows[0] if rows else None


def list_quotes(
    supa: "SupabaseClient",
    *,
    opportunity_id: str | None = None,
    insured_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """List quotes. Default order groups by opportunity (the Quotes module view).

    Filter by ``opportunity_id`` (one opp) or ``insured_id`` (all of a client's
    quotes across their opportunities — used by the proposal builder).
    """
    if opportunity_id:
        params = {"opportunity_id": f"eq.{opportunity_id}", "order": "created_at.desc"}
    elif insured_id:
        params = {"insured_id": f"eq.{insured_id}", "order": "line_of_business.asc,created_at.desc"}
    else:
        params = {"order": "opportunity_id.asc,created_at.desc"}
    return supa.select(TABLE, columns="*", params=params, limit=limit)
