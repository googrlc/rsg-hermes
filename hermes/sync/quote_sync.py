"""NowCerts quotes → Supabase opportunities pipeline sync.

In NowCerts a quote is a Policy row with ``isQuote=true`` (it carries a
``quote_number`` and its ``databaseId`` is the ``nowcerts_quote_guid``); see
``hermes/intake/opportunities.py``. This job pulls those quote rows from the AMS
and upserts them into the ``opportunities`` pipeline via the idempotent
``create_opportunity`` API, so AMS-sourced quotes surface in the pipeline
alongside the intake-created prospects — **with their live terms** (premium,
carrier, effective/expiration, status), not just an identifier.

Guarantees:
  * **Idempotent** per ``(client_identifier, line_of_business)`` — re-running
    never creates a duplicate opportunity.
  * **Never RESETS the human pipeline.** An existing opportunity's ``stage`` is
    never dragged backward — a Bound or Lost deal is never pulled to Quoted. A
    still-open row (New / Info Gathering / Quoting) IS promoted *forward* to
    Quoted, since a live quote means it's now quoted.
  * **Enriches with live terms.** premium_actual, carrier, effective_date,
    expiration_date, policy_status + the NowCerts identifiers are stamped on both
    new and existing rows. Schema-adaptive: term columns are written only if they
    exist, so the sync can't error before the quote-terms migration is applied.
  * **Additive** — never deletes. ``dry_run`` reports counts with zero writes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.intake import opportunities as opp
from hermes.core.field_utils import strip_date

log = logging.getLogger(__name__)

SYNC_SOURCE = "nowcerts_quote_sync"

# Stages that mean "not yet quoted" — a live quote promotes these forward to
# Quotes Received. Later stages (incl. Bound/Lost) are never reset backward.
_PRE_QUOTE_STAGES = {opp.STAGE_PREP, opp.STAGE_SENT_QUOTING}


@dataclass
class QuoteSyncResult:
    quotes_fetched: int = 0
    created: int = 0
    linked: int = 0
    enriched: int = 0
    promoted: int = 0
    skipped_incomplete: int = 0
    # Older six-month terms of a deal whose newest term we took instead.
    superseded_terms: int = 0
    # Quotes whose policy is already bound — AMS term artifacts, not deals.
    skipped_bound: int = 0
    # Register rows that are not open quotes: expired, bound, declined, inactive.
    skipped_closed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        return (
            f"quotes → opportunities: quotes={self.quotes_fetched} "
            f"created={self.created} linked={self.linked} enriched={self.enriched} "
            f"promoted={self.promoted} skipped_incomplete={self.skipped_incomplete} "
            f"superseded_terms={self.superseded_terms} skipped_bound={self.skipped_bound} "
            f"skipped_closed={self.skipped_closed} "
            f"errors={len(self.errors)}"
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


def _status(p: dict[str, Any]) -> str | None:
    return str(p.get("status") or p.get("Status") or "").strip() or None


# ---------------------------------------------------------------------------
# Enrichment helpers
# ---------------------------------------------------------------------------
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _discover_columns(supa: Any) -> set[str]:
    """Columns present on opportunities (from a sample row). Falls back to a
    superset so a missing quote-terms column can never error the write."""
    fallback = {
        "insured_id", "insured_name", "carrier", "quote_number", "nowcerts_quote_guid",
        "premium_actual", "effective_date", "expiration_date", "policy_status",
        "referral_source", "stage", "status", "synced_at", "sync_source",
    }
    try:
        rows = supa.select(opp.TABLE, columns="*", limit=1)
    except Exception:  # noqa: BLE001 — discovery must never abort the sync
        return fallback
    if rows and isinstance(rows[0], dict):
        return set(rows[0].keys())
    return fallback


def _enrichment_payload(q: dict[str, Any], cols: set[str], *, now_iso: str) -> dict[str, Any]:
    """Live quote terms + NowCerts identifiers to stamp on the opportunity.

    Identifier/carrier columns ship in the base pipeline migration, so they always
    write (non-None). The quote-terms columns are newer, so they're gated on
    ``cols`` (discovered live) — the sync can't error before that migration lands.
    """
    # Guaranteed columns (base opportunities schema).
    guaranteed = {
        "insured_id": _insured_guid(q),
        "quote_number": _quote_number(q),
        "nowcerts_quote_guid": _quote_guid(q),
        "carrier": _carrier(q),
    }
    payload = {k: v for k, v in guaranteed.items() if v is not None}
    # Quote-terms columns (added by 20260720170000_opportunity_quote_terms) — gated.
    # referral_source (from the opportunity-mirror migration) is READ-ONLY, pulled
    # from NowCerts here; gated too so a pre-migration DB can't error.
    terms = {
        "premium_actual": _premium(q),
        "effective_date": strip_date(q.get("effectiveDate") or q.get("EffectiveDate")),
        "expiration_date": strip_date(q.get("expirationDate") or q.get("ExpirationDate")),
        "policy_status": _status(q),
        "referral_source": q.get("referralSourceName") or q.get("referralSource") or None,
        "synced_at": now_iso,
        "sync_source": SYNC_SOURCE,
    }
    payload.update({k: v for k, v in terms.items() if v is not None and k in cols})
    return payload


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
# What belongs on the board: a quote that is still live and still out for a
# decision. Lamar, 2026-07-29: "we only want active received quotes in the CRM.
# those are actually renewal quotes."
#
# The register holds the agency's whole quote history — 104 rows, of which 89 are
# Expired and only 12 still active. Stages: Received 48, Bound 12, Declined 2,
# blank 42. Bound is won and Declined is lost; neither is an open opportunity,
# and an expired quote is a record of something that already happened.
#
# active + Received leaves 7, worth ~$14.7k, six of them renewals. That is the
# board. Everything else was the register being mistaken for a pipeline.
QUOTE_STAGE_RECEIVED = "Received"


def _is_open_quote(p: dict[str, Any]) -> bool:
    """True if this quote is still live and still awaiting a decision."""
    if p.get("active") is not True:
        return False
    return str(p.get("quoteStageName") or "").strip().lower() == QUOTE_STAGE_RECEIVED.lower()


def _business_type(p: dict[str, Any]) -> str | None:
    """The AMS's own word for what this quote is: Renewal, New Business, Rewrite.

    NowCerts records it and the board ignored it, typing all 60 rows New Business —
    37 of them on quotes the AMS explicitly calls Renewal. Mapped to the CRM's
    vocabulary; anything unrecognised returns None so the caller falls back rather
    than inventing a type.
    """
    raw = str(p.get("businessType") or p.get("BusinessType") or "").strip().lower()
    if not raw:
        return None
    if raw.startswith("renew"):
        return opp.TYPE_RENEWAL if hasattr(opp, "TYPE_RENEWAL") else "Renewals"
    if raw.startswith("new"):
        return opp.TYPE_NEW_BUSINESS
    if raw.startswith("rewrit"):
        return "Competitive Replacements (BOR)" if "Competitive Replacements (BOR)" in opp.OPPORTUNITY_TYPES else opp.TYPE_NEW_BUSINESS
    return None


def _effective(p: dict[str, Any]):
    """Sortable effective date, or None. Used to pick the term that matters."""
    raw = strip_date(p.get("effectiveDate") or p.get("EffectiveDate"))
    return raw or None


def _latest_term_per_deal(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One quote per client + line: the one with the latest effective date.

    Progressive personal auto runs six-month terms, so a single policy has up to
    four quote rows in the register. Every one of them resolved to the same
    opportunity (create_opportunity is idempotent per client + LOB + type), and each
    overwrote the last: `premium_estimate` stuck at whatever the FIRST term set,
    while the dates and quote guid followed the LAST. The result was 51 rows whose
    premium belongs to a different term than the dates beside it — Huff, Phyllis
    showing the 2026 premium against the 2025 term, and so on.

    Both cleanup runbooks read that as a join on the wrong key. There is no join
    here: the sync reads NowCerts directly. The defect is last-writer-wins across
    terms, so the fix is to decide which term this deal is about — the newest one —
    and take every field from that single record.
    """
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for q in quotes:
        name, lob = _insured_name(q), _lob(q)
        if not name or not lob:
            best[(f"__incomplete__{id(q)}", "")] = q     # keep, so it still counts as skipped
            continue
        key = (opp.make_client_identifier(name, _fein(q)), lob)
        current = best.get(key)
        if current is None or (_effective(q) or "") > (_effective(current) or ""):
            best[key] = q
    return list(best.values())


# Who works what, by line. Gretchen owns personal lines, Lamar owns commercial —
# how RSG actually splits the work. Everything unrecognised goes to Lamar rather
# than to nobody: an unowned deal is one nobody is accountable for, which is how
# 84 rows sat on a board with no owner at all.
#
# Written to assigned_to_email, NOT assigned_to. Both columns exist and the CRM
# reads the email one — filling the name column looks like it worked and shows
# nothing on the board.
_PERSONAL_LINES = {
    "personal auto", "homeowners", "motorcycle", "dwelling fire",
    "condo owners - personal", "personal umbrella", "mapd", "life",
}
GRETCHEN = "gretchen@risksolutionsgroup.net"
LAMAR = "lamar@risksolutionsgroup.net"


def _owner_for(lob: str | None) -> str:
    return GRETCHEN if str(lob or "").strip().lower() in _PERSONAL_LINES else LAMAR


def _stage_for(otype: str) -> str:
    """The "we have a quote in hand" stage on that type's ladder.

    Renewals run a different ladder entirely — no "Quotes Received" on it — so
    passing the AMS business type through without translating the stage raises
    `Unknown stage 'Quotes Received' for type 'Renewals'` on every renewal quote,
    which is most of the register. On the renewal ladder the equivalent point is
    Requote Renewal: we went to market and have a number back.
    """
    valid = opp.stages_for_type(otype)
    for candidate in (opp.STAGE_QUOTES_RECEIVED, "Requote Renewal"):
        if candidate in valid:
            return candidate
    return opp.default_stage_for_type(otype)


def _bound_policy_index(supa: Any) -> set[tuple[str, str, str]]:
    """(insured guid, LOB, effective date) for every bound policy in the book.

    A quote whose bound counterpart already exists is not an opportunity — it is
    the AMS's record of the term that was written. Those are the bulk of the
    phantom board: the register was loaded whole, quotes and bound terms alike.

    Best-effort: if the book cannot be read, the index is empty and nothing is
    suppressed. Filtering the board is worth doing, but not at the cost of the
    sync failing closed on a read it does not strictly need.
    """
    try:
        from hermes.ams import book as ams_book

        rows = ams_book.select_policies(
            supa, columns="nowcerts_insured_guid,lines_of_business,effective_date", limit=100000,
        )
    except Exception:  # noqa: BLE001
        log.exception("bound-policy index unavailable; not suppressing term artifacts")
        return set()
    out = set()
    for r in rows:
        guid = str(r.get("nowcerts_insured_guid") or "").strip()
        lob = str(r.get("lines_of_business") or "").strip()
        eff = str(r.get("effective_date") or "")[:10]
        if guid and lob and eff:
            out.add((guid, lob, eff))
    return out


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

    all_quotes = [p for p in nc.fetch_policies(since=since, page_size=page_size) if _is_quote(p)]
    quotes = [q for q in all_quotes if _is_open_quote(q)]
    result.skipped_closed = len(all_quotes) - len(quotes)
    if limit:
        quotes = quotes[:limit]
    result.quotes_fetched = len(quotes)
    # Collapse a client's multiple six-month terms to the one the deal is about,
    # so premium and dates come off the same record instead of two.
    quotes = _latest_term_per_deal(quotes)
    result.superseded_terms = max(0, result.quotes_fetched - len(quotes))
    bound = _bound_policy_index(supa)
    log.info("quote sync: %d NowCerts quotes to process (dry_run=%s)", len(quotes), dry_run)

    cols = _discover_columns(supa) if not dry_run else set()
    now_iso = _utcnow_iso()

    for q in quotes:
        name, lob = _insured_name(q), _lob(q)
        if not name or not lob:
            result.skipped_incomplete += 1
            log.info("SKIP quote (missing insured name or LOB): %s", _quote_number(q) or _quote_guid(q))
            continue

        if bound and (str(_insured_guid(q) or ""), lob, str(_effective(q) or "")[:10]) in bound:
            result.skipped_bound += 1
            log.info("SKIP quote (policy already bound): %s %s", name, lob)
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

            # A deal for this client and LOB already counts, whatever type it is.
            #
            # create_opportunity is idempotent per (client, LOB, TYPE) and this
            # sync always asks for New Business. So any client whose renewal was
            # already on the board got a second, duplicate deal: the type differed,
            # so the unique index let it through. That is how 43 of 108
            # opportunities ended up a Renewals row beside a New Business twin with
            # the same premium — the pipeline inflated by ~40%, renewals counted as
            # new business, and the two rows unmergeable because retyping one hits
            # the constraint.
            #
            # The dry-run branch above has always counted by (client, LOB) and
            # called these `linked`. The preview was right; the live path was not.
            prior = supa.select(
                opp.TABLE,
                columns="*",
                params={"client_identifier": f"eq.{client_identifier}",
                        "line_of_business": f"eq.{lob}"},
                limit=1,
            )
            if prior:
                # `linked`/`enriched` are counted once at the end of the loop for
                # every not-created row; counting here too double-reports it.
                row, created = prior[0], False
            else:
                otype = _business_type(q) or opp.TYPE_NEW_BUSINESS
                row, created = opp.create_opportunity(
                    supa,
                    client_identifier=client_identifier,
                    line_of_business=lob,
                    opportunity_type=otype,
                    insured_name=name,
                    insured_id=_insured_guid(q),
                    insured_type=_insured_type(q),
                    stage=_stage_for(otype),
                    premium_estimate=_premium(q),
                    carrier=_carrier(q),
                    source=SYNC_SOURCE,
                    assigned_to_email=_owner_for(lob),
                    # The board fell back to expiration_date when this was NULL,
                    # which is why every date on screen looked arbitrary. For a
                    # bind-by date the close date is the effective date.
                    expected_close_date=_effective(q),
                )

            # Stamp live terms + identifiers on both new and existing rows.
            payload = _enrichment_payload(q, cols, now_iso=now_iso)
            # Forward-only stage promotion: a still-open row becomes Quotes Received;
            # any later stage (Sent Proposal … Bound / Lost) is never dragged backward.
            if not created and row.get("stage") in _PRE_QUOTE_STAGES:
                payload["stage"] = opp.STAGE_QUOTES_RECEIVED
                payload["status"] = opp.status_for_stage(opp.STAGE_QUOTES_RECEIVED)
                result.promoted += 1
            if payload:
                supa.update(opp.TABLE, str(row.get("id")), payload)

            if created:
                result.created += 1
            else:
                result.linked += 1
                result.enriched += 1
        except Exception as exc:  # noqa: BLE001 — one bad quote shouldn't abort the run
            result.errors.append(f"quote {_quote_number(q) or _quote_guid(q)}: {exc}")
            log.warning("quote sync error on %s: %s", _quote_number(q) or _quote_guid(q), exc)

    log.info("quote sync done: %s", result.message)
    return result
