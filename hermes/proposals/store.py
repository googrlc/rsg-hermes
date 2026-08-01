"""Proposals — CRUD for standard client-facing proposals built from carrier quotes.

A proposal bundles selected ``opportunity_quotes`` rows (by id, in ``quote_ids``)
for one client. It spans one or many lines of business — commercial or personal.
This module owns the ``proposals`` table; rendering lives in ``generator.py`` and
Nextcloud filing in ``documents.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient

TABLE = "proposals"

STATUS_DRAFT = "Draft"
STATUS_FINAL = "Final"
STATUS_SENT = "Sent"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_proposal(
    supa: "SupabaseClient",
    *,
    insured_id: str | None = None,
    insured_name: str | None = None,
    client_identifier: str | None = None,
    opportunity_id: str | None = None,
    quote_ids: list[str] | None = None,
    title: str | None = None,
    segment: str | None = None,
    proposal_type: str = "New Business",
    notes: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a Draft proposal shell (before rendering)."""
    return supa.insert(
        TABLE,
        {
            "insured_id": insured_id,
            "insured_name": insured_name,
            "client_identifier": client_identifier,
            "opportunity_id": opportunity_id,
            "quote_ids": list(quote_ids or []),
            "title": title or (f"{insured_name} — Insurance Proposal" if insured_name else "Insurance Proposal"),
            "segment": segment,
            "proposal_type": proposal_type,
            "notes": notes,
            "status": STATUS_DRAFT,
            "created_by": created_by,
        },
    )


def update_render(
    supa: "SupabaseClient",
    proposal_id: str,
    *,
    content_html: str | None = None,
    total_premium: float | None = None,
    document_url: str | None = None,
    document_path: str | None = None,
    document_filename: str | None = None,
    pdf_url: str | None = None,
    pdf_path: str | None = None,
) -> dict[str, Any]:
    """Stamp render output (HTML/total and any filed document URLs) onto a proposal."""
    payload: dict[str, Any] = {
        k: v
        for k, v in {
            "content_html": content_html,
            "total_premium": total_premium,
            "document_url": document_url,
            "document_path": document_path,
            "document_filename": document_filename,
            "pdf_url": pdf_url,
            "pdf_path": pdf_path,
        }.items()
        if v is not None
    }
    if not payload:
        return {}
    payload["updated_at"] = _utcnow_iso()
    return supa.update(TABLE, proposal_id, payload)


def set_status(supa: "SupabaseClient", proposal_id: str, status: str) -> dict[str, Any]:
    return supa.update(TABLE, proposal_id, {"status": status, "updated_at": _utcnow_iso()})


def get_proposal(supa: "SupabaseClient", proposal_id: str) -> dict[str, Any] | None:
    rows = supa.select(TABLE, columns="*", params={"id": f"eq.{proposal_id}"}, limit=1)
    return rows[0] if rows else None


def list_proposals(
    supa: "SupabaseClient",
    *,
    insured_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"order": "created_at.desc"}
    if insured_id:
        params["insured_id"] = f"eq.{insured_id}"
    return supa.select(TABLE, columns="*", params=params, limit=limit)


def load_quotes(supa: "SupabaseClient", quote_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch the opportunity_quotes rows for the given ids (order preserved by caller)."""
    if not quote_ids:
        return []
    ids = ",".join(str(q) for q in quote_ids)
    rows = supa.select(
        "opportunity_quotes", columns="*",
        params={"id": f"in.({ids})"}, limit=len(quote_ids) + 10,
    )
    by_id = {str(r["id"]): r for r in rows}
    return [by_id[str(q)] for q in quote_ids if str(q) in by_id]
