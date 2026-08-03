"""The lead station — leads the agency owns, plus the prospects NowCerts holds.

A lead is someone worth calling who is not yet a deal. They live in ``crm_leads``
here, NOT in the AMS: a name from a networking event is not a record of insurance,
and a book full of prospects who never bought is a book that means nothing. A lead
reaches NowCerts by being converted to an opportunity and that opportunity being
won — the same rule the pipeline already follows.

The list also carries the prospects that were created directly in NowCerts, read
live and read-only (``source='nowcerts'``), so those are not invisible here. The
two are matched on the insured GUID when a CRM lead turns out to already exist in
the AMS, so nobody works the same person twice.

Perf note: NowCerts has no reliable ``$filter`` on prospectType, so this fetches
the insured list and filters client-side. Fine for the agency book size; if it
grows, cache into the canonical mirror (add prospectType to canonical_book_sync).
That read is also the slowest thing the backend does — so it is best-effort here.
The agency's OWN leads must never be hidden by the AMS being slow.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

TABLE = "crm_leads"
NOTES_TABLE = "crm_lead_notes"

SOURCE_CRM = "crm"
SOURCE_AMS = "nowcerts"

STATUS_NEW = "new"
STATUS_CONVERTED = "converted"
STATUS_LOST = "lost"
LEAD_STATUSES = ("new", "working", "quoted", STATUS_CONVERTED, STATUS_LOST)
# Statuses that take a lead off the working list — it has become a deal, or it is dead.
CLOSED_STATUSES = frozenset({STATUS_CONVERTED, STATUS_LOST})

# prospectType (Hot/Cold/Prospect) is a lead TEMPERATURE and is often blank on a
# NowCerts prospect. NowCerts actually marks prospect-vs-customer with the connector
# ``type`` code (1 = prospect, 0 = insured) — see hermes/intake/nowcerts_map. Detect a
# lead by EITHER signal so prospects created directly in the AMS (no temperature) show.
_PROSPECT_TYPE_VALUES = {"prospect", "hot_prospect", "cold_prospect", "hot prospect", "cold prospect"}
_PROSPECT_CODES = {"1", "prospect"}


def _is_prospect(ins: dict[str, Any]) -> bool:
    if str(ins.get("prospectType") or "").strip().lower() in _PROSPECT_TYPE_VALUES:
        return True
    if str(ins.get("type") or "").strip().lower() in _PROSPECT_CODES:
        return True
    p = ins.get("prospect")
    return p is True or str(p or "").strip().lower() == "true"


def _name(ins: dict[str, Any]) -> str:
    commercial = ins.get("commercialName") or ins.get("insuredCommercialName")
    if commercial:
        return str(commercial).strip()
    parts = [str(ins.get("firstName") or "").strip(), str(ins.get("lastName") or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _map_lead(ins: dict[str, Any]) -> dict[str, Any]:
    return {
        "insured_id": str(ins.get("id") or ins.get("databaseId") or "").strip() or None,
        "name": _name(ins),
        "prospect_type": ins.get("prospectType"),
        "insured_type": ins.get("insuredType"),
        "email": ins.get("eMail") or ins.get("email"),
        "phone": ins.get("phone") or ins.get("cellPhone"),
        "lead_source": ins.get("leadSources") or ins.get("referralSourceName"),
        # city was missing while the Leads list has always had a City column, so
        # every row rendered it blank.
        "city": ins.get("city"),
        "state": ins.get("state"),
    }


def _attach_x_dates(supa: Any, leads: list[dict[str, Any]]) -> None:
    """Hang each lead's x-date on it, in place.

    The x-date — when the coverage we are chasing expires — is the only date that
    ranks a lead list, and a NowCerts insured record does not carry one: it is a
    property of the policy they hold today, not of the person. Ours comes off the
    opportunity/quote mirror, which the quote sync fills from the NowCerts quote
    (``expiration_date``), keyed on the insured GUID.

    Soonest wins when a lead has several open lines — that is the one with a
    deadline. A lead with nothing in the pipeline has no x-date to show, and is
    left blank rather than given a guess.
    """
    by_insured = {str(lead.get("insured_id") or ""): lead for lead in leads if lead.get("insured_id")}
    if not by_insured:
        return
    try:
        rows = supa.select(
            "opportunities",
            columns="insured_id,line_of_business,expiration_date,stage",
            params={
                "insured_id": f"in.({','.join(by_insured)})",
                "status": "eq.open",
                "expiration_date": "not.is.null",
                "order": "expiration_date.asc",
            },
            limit=1000,
        )
    except Exception:  # noqa: BLE001 — a missing x-date must not empty the Leads list
        log.exception("leads: x-date lookup failed")
        return

    for row in rows:
        lead = by_insured.get(str(row.get("insured_id") or ""))
        # Ordered by expiration ascending, so the first row for a lead is the soonest.
        if lead is None or lead.get("x_date"):
            continue
        lead["x_date"] = str(row.get("expiration_date"))[:10]
        lead["x_date_line"] = row.get("line_of_business")


def list_prospects(nc: Any, supa: Any = None, *, limit: int = 200) -> dict[str, Any]:
    """Live NowCerts prospects → the Leads list. Read-only; caps at ``limit``.

    Pass ``supa`` to have each lead's x-date attached from the opportunity mirror.
    """
    leads: list[dict[str, Any]] = []
    for ins in nc.fetch_insureds(page_size=100):
        if _is_prospect(ins) and _name(ins):
            leads.append(_map_lead(ins))
            if len(leads) >= limit:
                break
    if supa is not None:
        _attach_x_dates(supa, leads)
    return {"count": len(leads), "leads": leads}


# ── The agency's own leads ────────────────────────────────────────────────────
# Fields a human may set. Everything else on the row is bookkeeping the API owns
# (status transitions on convert, the opportunity link, timestamps) — an editable
# `converted_opportunity_id` is a way to point a lead at somebody else's deal.
EDITABLE_FIELDS = (
    "name", "company", "email", "phone", "city", "state",
    "lead_type", "lines_of_business", "premium_estimate", "x_date",
    "status", "lead_source", "owner_email", "next_action", "next_action_date",
    "nowcerts_insured_guid", "lost_reason",
)


def create_lead(supa: Any, fields: dict[str, Any], *, created_by: str | None = None) -> dict[str, Any]:
    """Add a lead. Only ``name`` is required — a lead is often just a name and a
    number, and demanding more is how leads end up on a napkin instead."""
    payload = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS and v is not None}
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("a lead needs a name")
    payload["name"] = name
    status = str(payload.get("status") or STATUS_NEW)
    if status not in LEAD_STATUSES:
        raise ValueError(f"unknown status '{status}'; must be one of {list(LEAD_STATUSES)}")
    payload["status"] = status
    payload["created_by_email"] = created_by
    return supa.insert(TABLE, payload)


def update_lead(
    supa: Any,
    lead_id: str,
    fields: dict[str, Any],
    *,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Edit a lead. Unknown and API-owned fields are dropped rather than written.

    ``updated_by`` is recorded as an audit note so the history answers "who
    changed this, and when".
    """
    payload = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS}
    if not payload:
        raise ValueError("no editable fields provided")
    if "status" in payload and str(payload["status"]) not in LEAD_STATUSES:
        raise ValueError(f"unknown status '{payload['status']}'; must be one of {list(LEAD_STATUSES)}")
    if "name" in payload and not str(payload["name"] or "").strip():
        raise ValueError("a lead needs a name")
    row = supa.update(TABLE, lead_id, payload)
    # Append an audit trail entry so the history answers who changed what.
    # Best-effort: a failed note must not roll back a successful edit.
    try:
        changed = ", ".join(payload)
        body = f"Lead updated ({changed})"
        supa.insert(NOTES_TABLE, {"lead_id": lead_id, "body": body, "author_email": updated_by})
    except Exception:  # noqa: BLE001
        log.exception("leads: audit note failed after update: %s", lead_id)
    return row


def get_lead(supa: Any, lead_id: str) -> dict[str, Any] | None:
    rows = supa.select(TABLE, columns="*", params={"id": f"eq.{lead_id}"}, limit=1)
    return rows[0] if rows else None


def list_notes(supa: Any, lead_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    return supa.select(
        NOTES_TABLE, columns="*",
        params={"lead_id": f"eq.{lead_id}", "order": "created_at.desc"}, limit=limit,
    )


def add_note(supa: Any, lead_id: str, body: str, *, author_email: str | None = None) -> dict[str, Any]:
    """Write down what was said. Append-only — the history is the point."""
    text = str(body or "").strip()
    if not text:
        raise ValueError("a note needs a body")
    return supa.insert(NOTES_TABLE, {"lead_id": lead_id, "body": text, "author_email": author_email})


def list_crm_leads(supa: Any, *, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """The agency's own leads, soonest x-date first (the ones with a deadline)."""
    params: dict[str, str] = {"order": "x_date.asc.nullslast,created_at.desc"}
    if status:
        params["status"] = f"eq.{status}"
    rows = supa.select(TABLE, columns="*", params=params, limit=limit)
    for row in rows:
        row["source"] = SOURCE_CRM
    return rows


def combined_leads(
    supa: Any,
    nc: Any = None,
    *,
    status: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """The lead station: our leads first, then the AMS prospects we have not claimed.

    The NowCerts read is best-effort and deliberately second. It is the slowest
    call the backend makes and it has timed out in production; when it does, the
    agency's own leads still come back — an empty Leads screen because an upstream
    was slow is worse than a partial one that says so.
    """
    ours = list_crm_leads(supa, status=status, limit=limit)
    claimed = {str(r.get("nowcerts_insured_guid")) for r in ours if r.get("nowcerts_insured_guid")}

    prospects: list[dict[str, Any]] = []
    ams_error = None
    if nc is not None:
        try:
            prospects = list_prospects(nc, supa, limit=limit)["leads"]
        except Exception as exc:  # noqa: BLE001 — see the docstring
            log.exception("leads: NowCerts prospect read failed")
            ams_error = str(exc)
        for p in prospects:
            p["source"] = SOURCE_AMS
        # A prospect we already hold as a lead is one person, shown once — ours wins,
        # because ours is the one carrying the notes.
        prospects = [p for p in prospects if str(p.get("insured_id") or "") not in claimed]

    leads = ours + prospects
    return {
        "leads": leads,
        "count": len(leads),
        "crm_count": len(ours),
        "ams_count": len(prospects),
        # Named so the portal can say "the AMS list is missing" rather than quietly
        # showing a short list as though it were the whole one.
        "ams_error": ams_error,
        "statuses": list(LEAD_STATUSES),
    }


def convert_to_opportunity(
    supa: Any,
    lead_id: str,
    *,
    line_of_business: str,
    opportunity_type: str | None = None,
    stage: str | None = None,
    premium_estimate: float | None = None,
    assigned_to_email: str | None = None,
    next_action: str | None = None,
    expected_close_date: str | None = None,
    created_by: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Turn a lead into a pipeline opportunity. Returns ``(lead, opportunity, duplicate_detected)``.

    ``duplicate_detected`` is True when an opportunity for this client+LOB+type
    already existed before this call — the caller should warn the user that the
    deal was returned rather than freshly created, so they can verify it is the
    right one.

    This is the only way a lead moves forward, and it still does not touch
    NowCerts: the opportunity is worked in the CRM and reaches the AMS when it is
    won. A lead that never buys leaves no trace in the system of record, which is
    the whole reason leads are kept here.

    Idempotent through ``create_opportunity`` — converting twice returns the same
    deal rather than opening a second one.
    """
    from hermes_core import opportunities as opp

    lead = get_lead(supa, lead_id)
    if not lead:
        raise ValueError("lead not found")
    if not str(line_of_business or "").strip():
        raise ValueError("line_of_business is required to open a deal")

    name = lead.get("company") or lead.get("name")
    row, _created = opp.create_opportunity(
        supa,
        client_identifier=opp.make_client_identifier(name),
        line_of_business=line_of_business,
        opportunity_type=(opportunity_type or opp.TYPE_NEW_BUSINESS),
        insured_name=name,
        insured_id=lead.get("nowcerts_insured_guid"),
        insured_type=lead.get("lead_type"),
        stage=stage,
        premium_estimate=premium_estimate if premium_estimate is not None else lead.get("premium_estimate"),
        lead_source=lead.get("lead_source"),
        assigned_to_email=assigned_to_email or lead.get("owner_email"),
        next_action=next_action,
        expected_close_date=expected_close_date,
        # The x-date is why this deal has a deadline; carry it across so the
        # pipeline can date the card instead of starting blank.
        expiration_date=lead.get("x_date"),
        source="lead-conversion",
        created_by=created_by,
    )

    updated = supa.update(TABLE, lead_id, {
        "status": STATUS_CONVERTED,
        "converted_opportunity_id": row.get("id"),
        "converted_at": _utcnow_iso(),
    })

    # Log a timeline event on the opportunity so the audit history shows where
    # this deal came from, and who triggered the conversion.
    try:
        opp.log_event(
            supa, str(row.get("id")),
            event_type=opp.EVENT_CREATED if _created else opp.EVENT_NOTE,
            actor_email=created_by,
            summary=(
                f"Converted from lead {lead_id}"
                + (f" ({name})" if name else "")
                + (" — existing opportunity" if not _created else "")
            ),
            details={"lead_id": lead_id, "converted_by": created_by, "created": _created},
        )
    except Exception:  # noqa: BLE001 — conversion already succeeded; audit is best-effort
        log.exception("leads: opportunity event log failed after conversion: %s", lead_id)

    # Append an audit note to the lead itself so the lead's own history is complete.
    try:
        opp_id = str(row.get("id") or "")
        body = (
            f"Converted to opportunity on {line_of_business}"
            + (f" (opportunity {opp_id})" if opp_id else "")
            + (" — existing opportunity returned (duplicate detected)" if not _created else "")
        )
        supa.insert(NOTES_TABLE, {"lead_id": lead_id, "body": body, "author_email": created_by})
    except Exception:  # noqa: BLE001
        log.exception("leads: audit note failed after conversion: %s", lead_id)

    duplicate_detected = not _created
    return updated, row, duplicate_detected


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
