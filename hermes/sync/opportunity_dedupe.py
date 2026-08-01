"""Merge the duplicate deals the quote sync created.

For every client and line of business, the pipeline should hold one deal. It
held two for 43 of them: a Renewals row somebody worked in the CRM, and a New
Business twin the quote sync created five days later with the same premium.
``quote_sync`` no longer makes them (it now matches on client + LOB regardless
of type); this clears the ones already on the board.

**Which row wins.** The CRM row. It carries the human's judgment — the right
type, the stage they moved it to, the owner, any notes — and the sync row is a
machine's guess at a deal that already existed. But the sync row is not
worthless: it carries live NowCerts identifiers (``nowcerts_quote_guid``,
``quote_number``, ``insured_id``) and quote terms the CRM row may never have
had. So this is a merge, not a delete: anything the survivor is missing is
copied across from the twin before the twin goes.

Nothing is written unless ``apply=True``. The default prints what it would do,
because 43 pairs is not a diff anyone should take on trust.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hermes_core import opportunities as opp

log = logging.getLogger(__name__)

SYNC_SOURCE = "nowcerts_quote_sync"

# Fields worth rescuing from the twin before deleting it. Identifiers first —
# they are how the deal is matched back to the AMS — then the quote terms.
CARRY_OVER = (
    "insured_id", "quote_number", "nowcerts_quote_guid", "nowcerts_opportunity_id",
    "carrier", "premium_actual", "effective_date", "expiration_date", "policy_status",
)


@dataclass
class Pair:
    client_identifier: str
    line_of_business: str
    keep: dict[str, Any]
    drop: dict[str, Any]
    carried: dict[str, Any] = field(default_factory=dict)

    @property
    def same_premium(self) -> bool:
        return _num(self.keep.get("premium_estimate")) == _num(self.drop.get("premium_estimate"))

    def describe(self) -> str:
        prem = self.keep.get("premium_estimate")
        carried = ", ".join(sorted(self.carried)) or "nothing to carry"
        return (f"{self.client_identifier} · {self.line_of_business}: "
                f"keep {self.keep.get('opportunity_type')} ({self.keep.get('stage')}, ${prem}) "
                f"drop {self.drop.get('opportunity_type')} [{self.drop.get('sync_source')}] "
                f"— carrying {carried}")


@dataclass
class DedupeResult:
    pairs: list[Pair] = field(default_factory=list)
    merged: int = 0
    deleted: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        return (f"{len(self.pairs)} duplicate pairs, {self.merged} merged, "
                f"{self.deleted} removed, {len(self.skipped)} skipped, "
                f"{len(self.errors)} errors")


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _prefer(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(keep, drop) for a client+LOB group, or None if this isn't ours to touch.

    Only a group of exactly two where one came from the quote sync and one did
    not. Two human rows are a real decision someone made; three of anything is
    not a pattern this understands, and guessing on a pipeline is how you delete
    the deal somebody was working.
    """
    if len(rows) != 2:
        return None
    sync = [r for r in rows if str(r.get("sync_source") or "") == SYNC_SOURCE]
    human = [r for r in rows if str(r.get("sync_source") or "") != SYNC_SOURCE]
    if len(sync) != 1 or len(human) != 1:
        return None
    return human[0], sync[0]


def find_pairs(supa: Any, *, limit: int = 5000) -> list[Pair]:
    rows = supa.select(opp.TABLE, columns="*", limit=limit)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = (str(r.get("client_identifier") or ""), str(r.get("line_of_business") or ""))
        if not key[0] or not key[1]:
            continue
        groups.setdefault(key, []).append(r)

    pairs: list[Pair] = []
    for (ci, lob), group in sorted(groups.items()):
        chosen = _prefer(group)
        if not chosen:
            continue
        keep, drop = chosen
        carried = {
            f: drop.get(f) for f in CARRY_OVER
            if drop.get(f) not in (None, "") and keep.get(f) in (None, "")
        }
        pairs.append(Pair(ci, lob, keep, drop, carried))
    return pairs


def run_dedupe(supa: Any, *, apply: bool = False, require_same_premium: bool = True) -> DedupeResult:
    """Merge each duplicate pair into its CRM row. Read-only unless ``apply``."""
    result = DedupeResult(pairs=find_pairs(supa))

    for pair in result.pairs:
        # Differing premiums mean these may not be the same deal after all — the
        # sync may have found a genuinely new quote. Left alone and reported.
        if require_same_premium and not pair.same_premium:
            result.skipped.append(
                f"{pair.client_identifier} · {pair.line_of_business}: premiums differ "
                f"({pair.keep.get('premium_estimate')} vs {pair.drop.get('premium_estimate')})"
            )
            continue
        if not apply:
            continue
        try:
            if pair.carried:
                supa.update(opp.TABLE, str(pair.keep.get("id")), dict(pair.carried))
                result.merged += 1
            supa.delete(opp.TABLE, str(pair.drop.get("id")))
            result.deleted += 1
        except Exception as exc:  # noqa: BLE001 — one bad pair shouldn't stop the sweep
            result.errors.append(f"{pair.client_identifier} · {pair.line_of_business}: {exc}")
            log.warning("dedupe failed for %s/%s: %s", pair.client_identifier, pair.line_of_business, exc)

    log.info("opportunity dedupe: %s", result.message)
    return result


# ---------------------------------------------------------------------------
# Retiring what is not a live quote.
#
# The board carried the agency's whole quote history: 108 rows against 4 quotes
# NowCerts still calls open. The rest are bound, declined, expired, or duplicates
# of each other. They are not wrong values — they are records of things that
# already happened, and they belong off a pipeline.
#
# Everything removed is archived first, with its quotes, because an opportunity
# is not a leaf: agency quote rows hang off it and go when it goes.
# ---------------------------------------------------------------------------
QUOTES_CHILD_TABLE = "quotes"


@dataclass
class RetireResult:
    keep: list[dict[str, Any]] = field(default_factory=list)
    retire: list[dict[str, Any]] = field(default_factory=list)
    archived_path: str | None = None
    deleted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        prem = sum(_num(r.get("premium_estimate")) or 0 for r in self.retire)
        return (f"{len(self.keep)} live quotes kept, {len(self.retire)} retired "
                f"(${prem:,.0f} of closed/duplicate records); deleted={self.deleted}; "
                f"errors={len(self.errors)}")


def open_quote_guids(nc: Any) -> set[str]:
    """Quote guids the AMS still reports as open — status Active, stage Received."""
    from hermes.sync.quote_sync import _is_open_quote, _is_quote, _quote_guid

    return {str(_quote_guid(q)) for q in nc.fetch_policies()
            if _is_quote(q) and _is_open_quote(q) and _quote_guid(q)}


def open_quote_deals(nc: Any) -> set[tuple[str, str]]:
    """(client identifier, LOB) for every open quote.

    The guid alone is not enough to decide a row is dead. Board rows still carry
    whichever term's guid the old sync happened to write last, and that is often
    a superseded six-month term rather than the live one. Retiring on the guid
    took Tiffany Lombardo's live $3,213 renewal off the board because her row
    pointed at the $2,972 term before it — a live deal deleted for holding a
    stale pointer.

    So a row survives if its client has an open quote on that line, whichever
    term the row itself names.
    """
    from hermes.sync.quote_sync import _fein, _insured_name, _is_open_quote, _is_quote, _lob

    out: set[tuple[str, str]] = set()
    for q in nc.fetch_policies():
        if not (_is_quote(q) and _is_open_quote(q)):
            continue
        name, lob = _insured_name(q), _lob(q)
        if name and lob:
            out.add((opp.make_client_identifier(name, _fein(q)), lob))
    return out


def plan_retirement(supa: Any, nc: Any) -> RetireResult:
    """Split the board into what the AMS still calls live, and everything else."""
    result = RetireResult()
    live_guids = open_quote_guids(nc)
    live_deals = open_quote_deals(nc)
    for row in supa.select(opp.TABLE, columns="*", limit=5000):
        guid = str(row.get("nowcerts_quote_guid") or "")
        deal = (str(row.get("client_identifier") or ""), str(row.get("line_of_business") or ""))
        # A row with no quote guid was not created from the register — a lead
        # conversion, a hand-entered deal. Not this sweep's business.
        if not guid:
            result.keep.append(row)
        elif guid in live_guids or deal in live_deals:
            result.keep.append(row)
        else:
            result.retire.append(row)
    return result


def run_retirement(supa: Any, nc: Any, *, apply: bool = False,
                   backup_dir: str = "/var/lib/hermes") -> RetireResult:
    from hermes.sync.quote_board_repair import backup

    result = plan_retirement(supa, nc)
    if not apply or not result.retire:
        return result

    # Archive the rows AND their quotes before anything goes.
    payload: list[dict[str, Any]] = []
    for row in result.retire:
        record = dict(row)
        try:
            record["_quotes"] = supa.select(
                QUOTES_CHILD_TABLE, columns="*",
                params={"opportunity_id": f"eq.{row.get('id')}"}, limit=200,
            )
        except Exception:  # noqa: BLE001 — no quotes table, or none attached
            record["_quotes"] = []
        payload.append(record)
    result.archived_path = backup(payload, backup_dir)

    for row in result.retire:
        try:
            supa.delete(opp.TABLE, str(row.get("id")))
            result.deleted += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{row.get('insured_name')}: {exc}")
            log.warning("retire failed for %s: %s", row.get("insured_name"), exc)
    log.info("opportunity retirement: %s", result.message)
    return result
