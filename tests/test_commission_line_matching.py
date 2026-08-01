"""Tests for statement-line matching (hermes.commissions.matching).

The ladder decides where real money lands. Step 3 (create-from-book) is what
reconciles the 2026 seeding floor with carrier statements that ignore it; step 4
must stay dumb, because anything clever enough to resolve a $0.00 filler row
like "99999999" is clever enough to attach real money to the wrong client.
"""

from __future__ import annotations

import pytest

from hermes.commissions import matching as ing


def line(policy_number="P1", **kw):
    base = {"id": "T1", "policy_number": policy_number, "commission_amount": 100,
            "transaction_date": "2026-03-01", "insured_name": "Acme"}
    base.update(kw)
    return base


def book_policy(policy_number="P1", **kw):
    base = {"policy_number": policy_number, "policy_guid": f"g-{policy_number}",
            "carrier": "Progressive", "lines_of_business": "Personal Auto",
            "effective_date": "2025-06-01", "expiration_date": "2026-06-01",
            "premium_amount": 1200, "active": True, "status": "Active"}
    base.update(kw)
    return base


# --- normalization -----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("MC969126179", "MC969126179"),
    ("mc-969 126 179", "MC969126179"),
    ("  969126179  ", "969126179"),
    ("OSIH3GA_01549594-1", "OSIH3GA015495941"),
    (None, ""), ("", ""), ("---", ""),
])
def test_normalize(raw, expected):
    assert ing.normalize_policy_number(raw) == expected


# --- the ladder --------------------------------------------------------------

def ladder(ledger_exact=None, ledger_norm=None, book=None):
    return {
        "ledger_by_exact": ledger_exact or {},
        "ledger_by_normalized": ledger_norm or {},
        "book_by_normalized": book or {},
    }


def test_exact_match_wins():
    m = ing.match_line(line("P1"), **ladder(ledger_exact={"P1": "L1"}))
    assert m.kind == ing.MATCH_EXACT and m.ledger_id == "L1" and m.linked


def test_normalized_match_when_punctuation_differs():
    m = ing.match_line(line("mc-969 126 179"),
                       **ladder(ledger_norm={"MC969126179": "L9"}))
    assert m.kind == ing.MATCH_NORMALIZED and m.ledger_id == "L9"


def test_exact_is_preferred_over_normalized():
    m = ing.match_line(line("P1"),
                       **ladder(ledger_exact={"P1": "EXACT"},
                                ledger_norm={"P1": "NORM"}))
    assert m.ledger_id == "EXACT"


def test_policy_in_the_book_with_no_ledger_row_is_created():
    """The floor governs seeding; a statement line always lands."""
    m = ing.match_line(line("861340462"),
                       **ladder(book={"861340462": book_policy("861340462")}))
    assert m.kind == ing.MATCH_CREATED
    assert m.ledger_id is None          # the CALLER writes it
    assert m.policy_number == "861340462"
    assert not m.linked


def test_created_uses_the_books_spelling_of_the_policy_number():
    m = ing.match_line(line("mc-969 126 179"),
                       **ladder(book={"MC969126179": book_policy("MC969126179")}))
    assert m.policy_number == "MC969126179"


def test_no_policy_anywhere_is_unmatched():
    m = ing.match_line(line("99999999"), **ladder())
    assert m.kind == ing.MATCH_UNMATCHED and not m.linked


def test_the_real_filler_rows_stay_unmatched():
    """99999999 (13 lines) and 874308795 (3 lines), both $0.00, are statement
    filler and absent from the book. They must NOT be resolved."""
    for filler in ("99999999", "874308795"):
        m = ing.match_line(line(filler, commission_amount=0),
                           **ladder(ledger_exact={"REAL": "L1"},
                                    book={"REALKEY": book_policy("REAL")}))
        assert m.kind == ing.MATCH_UNMATCHED


def test_blank_policy_number_is_unmatched_not_a_collision():
    m = ing.match_line(line(""), **ladder(ledger_exact={"": "L1"}))
    assert m.kind == ing.MATCH_UNMATCHED


def test_punctuation_only_policy_number_is_unmatched():
    m = ing.match_line(line("---"), **ladder(ledger_norm={"": "L1"}))
    assert m.kind == ing.MATCH_UNMATCHED


# --- indexing ----------------------------------------------------------------

def test_ambiguous_normalized_keys_are_dropped_not_guessed():
    """Two ledger rows folding to the same key: refuse to pick one."""
    exact, norm = ing._index_ledger([
        {"id": "L1", "policy_number": "AB-123"},
        {"id": "L2", "policy_number": "AB123"},
    ])
    assert exact == {"AB-123": "L1", "AB123": "L2"}
    assert "AB123" not in norm        # ambiguous -> no normalized match


def test_same_row_listed_twice_is_not_a_collision():
    _, norm = ing._index_ledger([
        {"id": "L1", "policy_number": "AB-123"},
        {"id": "L1", "policy_number": "AB123"},
    ])
    assert norm["AB123"] == "L1"


def test_rows_without_a_policy_number_are_skipped():
    exact, norm = ing._index_ledger([{"id": "L1", "policy_number": ""}])
    assert exact == {} and norm == {}


# --- the row we create -------------------------------------------------------

def test_created_row_carries_the_statement_origin():
    row = ing.ledger_row_from_policy(book_policy("X1"), line("X1"))
    assert row["origin"] == ing.ORIGIN_STATEMENT
    assert row["statement_source"] == ing.ORIGIN_STATEMENT
    assert row["reconciliation_status"] == "pending"


def test_created_row_never_invents_an_expected_commission():
    """The nightly seed owns the expected side. Guessing it here would put a
    fabricated expectation on a money surface."""
    row = ing.ledger_row_from_policy(book_policy(), line())
    assert "expected_commission" not in row


def test_created_row_takes_policy_facts_from_the_book_not_the_statement():
    row = ing.ledger_row_from_policy(
        book_policy(carrier="Safeco"), line(carrier_name="MISREAD"),
    )
    assert row["carrier_name"] == "Safeco"


# --- the driver --------------------------------------------------------------

class FakeSupa:
    def __init__(self, txns, ledger):
        self.tables = {ing.TRANSACTIONS_TABLE: txns, ing.LEDGER_TABLE: ledger}
        self.updates: list[tuple[str, dict]] = []
        self.inserts: list[dict] = []
        self._seq = 0

    def select(self, table, *, columns="*", params=None, limit=1000):
        return [dict(r) for r in self.tables.get(table, [])][:limit]

    def insert(self, table, payload):
        self._seq += 1
        row = {"id": f"NEW{self._seq}", **payload}
        self.tables.setdefault(table, []).append(row)
        self.inserts.append(row)
        return dict(row)

    def update(self, table, record_id, payload):
        self.updates.append((record_id, payload))
        return {"id": record_id, **payload}


def _run(monkeypatch, txns, ledger, book, **kw):
    supa = FakeSupa(txns, ledger)
    monkeypatch.setattr("hermes_core.book.select_policies", lambda *a, **k: book)
    return supa, ing.relink_unmatched(supa, **kw)


def test_relink_links_an_exact_match(monkeypatch):
    supa, out = _run(monkeypatch,
                     [line("P1", id="T1", ledger_id=None)],
                     [{"id": "L1", "policy_number": "P1"}], [])
    assert out.exact == 1 and out.linked == 1 and out.balanced
    assert supa.updates == [("T1", {"ledger_id": "L1"})]


def test_relink_skips_lines_that_already_have_a_ledger(monkeypatch):
    supa, out = _run(monkeypatch,
                     [line("P1", id="T1", ledger_id="L1")],
                     [{"id": "L1", "policy_number": "P1"}], [])
    assert out.examined == 0 and supa.updates == []


def test_relink_creates_one_ledger_row_for_many_orphaned_lines(monkeypatch):
    """16 lines on one policy must not create 16 ledger rows."""
    txns = [line("861340462", id=f"T{i}", ledger_id=None) for i in range(16)]
    supa, out = _run(monkeypatch, txns, [], [book_policy("861340462")])
    assert out.created == 16
    assert out.ledger_rows_created == 1
    assert len(supa.inserts) == 1
    assert {u[1]["ledger_id"] for u in supa.updates} == {"NEW1"}


def test_relink_leaves_filler_rows_alone(monkeypatch):
    supa, out = _run(monkeypatch,
                     [line("99999999", id="T1", ledger_id=None, commission_amount=0)],
                     [], [])
    assert out.unmatched == 1 and out.linked == 0 and out.balanced
    assert supa.updates == [] and supa.inserts == []
    assert out.unmatched_policies == {"99999999": 1}


def test_dry_run_writes_nothing(monkeypatch):
    supa, out = _run(monkeypatch,
                     [line("861340462", id="T1", ledger_id=None)],
                     [], [book_policy("861340462")], dry_run=True)
    assert out.created == 1
    assert supa.inserts == [] and supa.updates == []
    assert "dry-run" in out.message


def test_every_line_is_accounted_for(monkeypatch):
    txns = [
        line("P1", id="T1", ledger_id=None),          # exact
        line("mc-1", id="T2", ledger_id=None),        # normalized
        line("BOOKONLY", id="T3", ledger_id=None),    # created
        line("99999999", id="T4", ledger_id=None),    # unmatched
    ]
    _, out = _run(monkeypatch, txns,
                  [{"id": "L1", "policy_number": "P1"}, {"id": "L2", "policy_number": "MC1"}],
                  [book_policy("BOOKONLY")])
    assert (out.exact, out.normalized, out.created, out.unmatched) == (1, 1, 1, 1)
    assert out.balanced


def test_a_book_read_failure_disables_creation_without_losing_exact_matches(monkeypatch):
    supa = FakeSupa([line("P1", id="T1", ledger_id=None),
                     line("BOOKONLY", id="T2", ledger_id=None)],
                    [{"id": "L1", "policy_number": "P1"}])

    def boom(*a, **k):
        raise RuntimeError("AMS down")

    monkeypatch.setattr("hermes_core.book.select_policies", boom)
    out = ing.relink_unmatched(supa)
    assert out.exact == 1                 # still linked
    assert out.unmatched == 1             # creation unavailable
    assert out.errors and "book read" in out.errors[0]
