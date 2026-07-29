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

from hermes.intake import opportunities as opp

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
