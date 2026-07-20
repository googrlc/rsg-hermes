"""NowCerts quotes → Supabase opportunities pipeline sync.

In NowCerts a quote is a Policy row with ``isQuote=true`` (it carries a
``quote_number`` and its ``databaseId`` is the ``nowcerts_quote_guid``); see
``hermes/intake/opportunities.py``. This job pulls those quote rows from the AMS
and upserts them into the ``opportunities`` pipeline via the idempotent
``create_opportunity`` API, so AMS-sourced quotes surface in the pipeline
alongside the intake-created prospects.

Guarantees:
  * **Idempotent** per ``(client_identifier, line_of_business)`` — re-running
    never creates a duplicate opportunity.
  * **Respects the human pipeline.** An existing opportunity's ``stage`` is NEVER
    reset — a Bound or Lost deal is never dragged back to Quoted. Only the
    NowCerts identifiers (insured/quote guid + number) are backfilled via
    ``link_nowcerts``.
  * **Additive** — never deletes. ``dry_run`` reports counts with zero writes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hermes.intake import opportunities as opp

log = logging.getLogger(__name__)


@dataclass
class QuoteSyncResult:
    quotes_fetched: int = 0
    created: int = 0
    linked: int = 0
    skipped_incomplete: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        return (
            f"quotes → opportunities: quotes={self.quotes_fetched} "
            f"created={self.created} linked={self.linked} "
            f"skipped_incomplete={self.skipped_incomplete} errors={len(self.errors)}"
        )


# ---------------------------------------------------------------------------
# NowCerts quote-row extraction (a quote is a Policy row with isQuote=true)
# ---------------------------------------------------------------------------
def _is_quote(p: dict[str, Any]) -> bool:
    for key in ("isQuote", "IsQuote", "is_quote"):
        if isinstance(p.get(key), bool):
            return p[key]
    return False


def _quote_guid(p: dict[str, Any]) -> str | None:
    return str(p.get("databaseId") or p.get("DatabaseId") or p.get("id") or "").strip() or None


def _quote_number(p: dict[str, Any]) -> str | None:
    return str(p.get("number") or p.get("policyNumber") or p.get("Number") or "").strip() or None


def _insured_guid(p: dict[str, Any]) -> str | None:
    return str(p.get("insuredDatabaseId") or p.get("insuredId") or "").strip() or None


def _insured_name(p: dict[str, Any]) -> str:
    commercial = p.get("insuredCommercialName") or p.get("commercialName")
    if commercial:
        return str(commercial).strip()
    parts = [str(p.get("insuredFirstName") or "").strip(), str(p.get("insuredLastName") or "").strip()]
    person = " ".join(x for x in parts if x).strip()
    return person or str(p.get("insuredName") or "").strip()


def _fein(p: dict[str, Any]) -> str | None:
    return str(p.get("insuredFEIN") or p.get("fein") or "").strip() or None


def _lob(p: dict[str, Any]) -> str | None:
    lob_list = p.get("lineOfBusinesses")
    if isinstance(lob_list, list) and lob_list and isinstance(lob_list[0], dict):
        name = lob_list[0].get("lineOfBusinessName")
        if name:
            return str(name).strip()
    for key in ("lineOfBusinessName", "lineOfBusiness", "LineOfBusinessName"):
        if p.get(key):
            return str(p[key]).strip()
    return None


def _insured_type(p: dict[str, Any]) -> str | None:
    raw = p.get("insuredType") or p.get("insured_type")
    if not raw:
        return None
    val = str(raw).strip().capitalize()
    return val if val in opp.INSURED_TYPES else None


def _premium(p: dict[str, Any]) -> float | None:
    for key in ("totalPremium", "premium", "Premium", "premiumEstimate"):
        val = p.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _carrier(p: dict[str, Any]) -> str | None:
    return str(p.get("carrierName") or p.get("CarrierName") or p.get("carrier") or "").strip() or None


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def run_quote_sync(
    nc: Any,
    supa: Any,
    *,
    since: str | None = None,
    dry_run: bool = False,
    page_size: int = 100,
    limit: int | None = None,
) -> QuoteSyncResult:
    """Pull NowCerts quote rows (Policy with isQuote=true) and upsert opportunities.

    Args:
        nc: NowCertsClient.
        supa: SupabaseClient.
        since: ISO datetime — only quotes changed since (changeDate >= since).
        dry_run: classify + report only, no writes.
        page_size: NowCerts OData page size.
        limit: optional cap on quotes processed (testing/safety).
    """
    result = QuoteSyncResult()

    quotes = [p for p in nc.fetch_policies(since=since, page_size=page_size) if _is_quote(p)]
    if limit:
        quotes = quotes[:limit]
    result.quotes_fetched = len(quotes)
    log.info("quote sync: %d NowCerts quotes to process (dry_run=%s)", len(quotes), dry_run)

    for q in quotes:
        name, lob = _insured_name(q), _lob(q)
        if not name or not lob:
            result.skipped_incomplete += 1
            log.info("SKIP quote (missing insured name or LOB): %s", _quote_number(q) or _quote_guid(q))
            continue

        client_identifier = opp.make_client_identifier(name, _fein(q))
        try:
            if dry_run:
                existing = supa.select(
                    opp.TABLE, columns="id",
                    params={"client_identifier": f"eq.{client_identifier}",
                            "line_of_business": f"eq.{lob}"},
                    limit=1,
                )
                if existing:
                    result.linked += 1
                else:
                    result.created += 1
                continue

            row, created = opp.create_opportunity(
                supa,
                client_identifier=client_identifier,
                line_of_business=lob,
                opportunity_type=opp.TYPE_NEW_BUSINESS,
                insured_name=name,
                insured_id=_insured_guid(q),
                insured_type=_insured_type(q),
                stage=opp.STAGE_QUOTES_RECEIVED,
                premium_estimate=_premium(q),
                carrier=_carrier(q),
                source="nowcerts_quote_sync",
            )
            # Backfill NowCerts identifiers on both new and existing rows. This
            # never touches stage/status, so a human-advanced pipeline is safe.
            opp.link_nowcerts(
                supa, str(row.get("id")),
                insured_id=_insured_guid(q),
                quote_number=_quote_number(q),
                nowcerts_quote_guid=_quote_guid(q),
            )
            if created:
                result.created += 1
            else:
                result.linked += 1
        except Exception as exc:  # noqa: BLE001 — one bad quote shouldn't abort the run
            result.errors.append(f"quote {_quote_number(q) or _quote_guid(q)}: {exc}")
            log.warning("quote sync error on %s: %s", _quote_number(q) or _quote_guid(q), exc)

    log.info("quote sync done: %s", result.message)
    return result
