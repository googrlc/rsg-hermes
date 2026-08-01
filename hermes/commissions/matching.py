"""Statement-line matching — attaching commission transactions to ledger rows.

A parsed statement line is money that actually arrived. It has to land
somewhere, and the four ways it can land are ordered by how much we trust them:

  1. EXACT      policy_number matches a ledger row outright.
  2. NORMALIZED matches after trimming, case-folding and stripping punctuation.
  3. CREATED    no ledger row, but the policy is in the book — so make one,
                stamped origin='statement'.
  4. UNMATCHED  no policy anywhere. A human decides. Never auto-resolved.

Step 3 is the one that reconciles the seeding floor with reality. RSG seeds
commission only for business effective 2026-01-01 or later, but carrier
statements do not respect our reporting window: of the 90 orphaned lines sitting
in commission_transactions, 53 are dated pre-2026 and 74 name a policy that IS
in the book. Money that arrived is a fact; the reporting window is a preference.
The fact wins, and origin='statement' keeps the distinction legible.

Step 4 must stay dumb. Two of the 18 orphaned policy numbers are ``99999999``
(13 lines) and ``874308795`` (3 lines), both totalling exactly $0.00 and absent
from the book — statement filler, not policies. Anything clever enough to
"resolve" those is clever enough to attach real money to the wrong client.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

LEDGER_TABLE = "commission_ledger"
TRANSACTIONS_TABLE = "commission_transactions"

ORIGIN_STATEMENT = "statement"

MATCH_EXACT = "exact"
MATCH_NORMALIZED = "normalized"
MATCH_CREATED = "created"
MATCH_UNMATCHED = "unmatched"


def normalize_policy_number(value: Any) -> str:
    """Fold a policy number to a comparable key.

    Carriers punctuate inconsistently across statements and the AMS — a policy
    is ``MC969126179`` in one place and ``mc-969 126 179`` in another. Strip
    everything that isn't alphanumeric and upper-case the rest.

    Returns "" for anything empty, which never matches: an empty key colliding
    with another empty key would attach money to an arbitrary row.
    """
    text = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    return text.upper()


@dataclass
class MatchResult:
    kind: str
    ledger_id: str | None = None
    policy_number: str | None = None
    reason: str = ""

    @property
    def linked(self) -> bool:
        return self.ledger_id is not None


def match_line(
    line: dict[str, Any],
    *,
    ledger_by_exact: dict[str, str],
    ledger_by_normalized: dict[str, str],
    book_by_normalized: dict[str, dict[str, Any]],
) -> MatchResult:
    """Walk the ladder for one statement line. Pure — creates nothing.

    ``MATCH_CREATED`` means "a ledger row SHOULD be created from this book
    policy", not that one was; the caller does the writing.
    """
    raw = str(line.get("policy_number") or "").strip()
    if not raw:
        return MatchResult(MATCH_UNMATCHED, reason="statement line carries no policy number")

    ledger_id = ledger_by_exact.get(raw)
    if ledger_id:
        return MatchResult(MATCH_EXACT, ledger_id, raw, "policy_number matched a ledger row")

    key = normalize_policy_number(raw)
    if not key:
        return MatchResult(MATCH_UNMATCHED, policy_number=raw,
                           reason="policy number normalizes to nothing")

    ledger_id = ledger_by_normalized.get(key)
    if ledger_id:
        return MatchResult(MATCH_NORMALIZED, ledger_id, raw,
                           "matched a ledger row after normalizing punctuation/case")

    policy = book_by_normalized.get(key)
    if policy:
        return MatchResult(
            MATCH_CREATED, None, str(policy.get("policy_number") or raw),
            "policy is in the book but has no ledger row — money arrived outside "
            "the seeding floor",
        )

    return MatchResult(MATCH_UNMATCHED, policy_number=raw,
                       reason="no policy in the ledger or the book")


def _index_ledger(rows: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """(exact policy_number -> id, normalized -> id).

    A normalized key that two different ledger rows share is dropped from the
    normalized index: an ambiguous match is not a match, and guessing which of
    two policies got paid is exactly the mistake worth avoiding.
    """
    exact: dict[str, str] = {}
    normalized: dict[str, str] = {}
    collisions: set[str] = set()

    for row in rows:
        number = str(row.get("policy_number") or "").strip()
        row_id = str(row.get("id") or "")
        if not number or not row_id:
            continue
        exact.setdefault(number, row_id)
        key = normalize_policy_number(number)
        if not key:
            continue
        if key in normalized and normalized[key] != row_id:
            collisions.add(key)
        else:
            normalized.setdefault(key, row_id)

    for key in collisions:
        normalized.pop(key, None)
    if collisions:
        log.warning("commission ingest: %d ambiguous normalized policy keys ignored",
                    len(collisions))
    return exact, normalized


def _index_book(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = normalize_policy_number(row.get("policy_number"))
        if key:
            out.setdefault(key, row)
    return out


def ledger_row_from_policy(policy: dict[str, Any], line: dict[str, Any]) -> dict[str, Any]:
    """The ledger row a statement line implies for a book policy.

    Expected commission is left NULL on purpose. The nightly seed owns the
    expected side and will fill it if the policy qualifies; inventing a number
    here would put a fabricated expectation on a money surface.
    """
    return {
        "policy_number": policy.get("policy_number"),
        "nowcerts_policy_id": policy.get("policy_guid"),
        "carrier_name": policy.get("carrier") or line.get("carrier_name"),
        "lob": policy.get("lines_of_business") or line.get("lob"),
        "client_name": line.get("insured_name"),
        "policy_effective_date": policy.get("effective_date"),
        "policy_expiration_date": policy.get("expiration_date"),
        "gross_premium": policy.get("premium_amount") or policy.get("annualized_premium"),
        "statement_date": line.get("transaction_date") or policy.get("effective_date"),
        "statement_source": ORIGIN_STATEMENT,
        "origin": ORIGIN_STATEMENT,
        "reconciliation_status": "pending",
    }


@dataclass
class LinkRun:
    examined: int = 0
    exact: int = 0
    normalized: int = 0
    created: int = 0
    unmatched: int = 0
    ledger_rows_created: int = 0
    dry_run: bool = False
    unmatched_policies: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def linked(self) -> int:
        return self.exact + self.normalized + self.created

    @property
    def balanced(self) -> bool:
        return self.linked + self.unmatched == self.examined

    @property
    def message(self) -> str:
        return (
            f"commission line linking ({'dry-run' if self.dry_run else 'live'}): "
            f"examined={self.examined} exact={self.exact} normalized={self.normalized} "
            f"created={self.created} (ledger rows +{self.ledger_rows_created}) "
            f"unmatched={self.unmatched} balanced={self.balanced} "
            f"errors={len(self.errors)}"
        )


def relink_unmatched(
    supa: "SupabaseClient",
    *,
    dry_run: bool = False,
    limit: int = 50000,
) -> LinkRun:
    """Run the ladder over transactions that never got a ``ledger_id``.

    Idempotent: a line that already has a ledger_id is not examined, and a
    policy needing a ledger row gets exactly one no matter how many of its lines
    are orphaned.
    """
    from hermes_core import book as ams_book

    result = LinkRun(dry_run=dry_run)

    orphans = [
        r for r in supa.select(TRANSACTIONS_TABLE, columns="*", limit=limit)
        if not r.get("ledger_id")
    ]
    if not orphans:
        return result

    ledger = supa.select(LEDGER_TABLE, columns="id,policy_number", limit=limit)
    exact_idx, norm_idx = _index_ledger(ledger)

    try:
        book = ams_book.select_policies(
            supa,
            columns="policy_number,policy_guid,carrier,lines_of_business,status,active,"
                    "effective_date,expiration_date,premium_amount,annualized_premium",
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 — without the book, step 3 is simply unavailable
        log.exception("commission ingest: book read failed; create-from-book disabled")
        result.errors.append(f"book read: {exc}")
        book = []
    book_idx = _index_book(book)

    created_for: dict[str, str] = {}   # normalized key -> new ledger id

    for line in orphans:
        result.examined += 1
        match = match_line(
            line,
            ledger_by_exact=exact_idx,
            ledger_by_normalized=norm_idx,
            book_by_normalized=book_idx,
        )

        if match.kind == MATCH_UNMATCHED:
            result.unmatched += 1
            key = match.policy_number or "(blank)"
            result.unmatched_policies[key] = result.unmatched_policies.get(key, 0) + 1
            continue

        ledger_id = match.ledger_id

        if match.kind == MATCH_CREATED:
            key = normalize_policy_number(match.policy_number)
            ledger_id = created_for.get(key)
            if ledger_id is None:
                if dry_run:
                    result.created += 1
                    created_for[key] = "(dry-run)"
                    continue
                try:
                    row = supa.insert(
                        LEDGER_TABLE, ledger_row_from_policy(book_idx[key], line)
                    )
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"create ledger for {match.policy_number}: {exc}")
                    continue
                ledger_id = str(row.get("id"))
                created_for[key] = ledger_id
                result.ledger_rows_created += 1
            # The indexes are deliberately NOT updated with the new row. A match
            # kind describes the line's relationship to the state we STARTED in,
            # so all 16 orphaned lines on one policy report as `created` rather
            # than 1 created + 15 exact. `created_for` is what stops us writing
            # 16 ledger rows; `ledger_rows_created` reports the actual writes.
            result.created += 1
        elif match.kind == MATCH_EXACT:
            result.exact += 1
        else:
            result.normalized += 1

        if not dry_run and ledger_id and ledger_id != "(dry-run)":
            try:
                supa.update(TRANSACTIONS_TABLE, str(line.get("id")), {"ledger_id": ledger_id})
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"link txn {line.get('id')}: {exc}")

    log.info("%s", result.message)
    return result
