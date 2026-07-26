"""Tests for the commission surface reads.

The bug this guards against: the Commissions view rendered an empty table
because every ledger row failed the `reconciled` filter, and the empty table
was indistinguishable from "there is no commission data". A read must always
carry the counts and the coverage, whatever the filter matches.
"""

from __future__ import annotations

import pytest

from hermes.commissions import surface

SINCE = "2026-01-01"


def pol(**kw):
    base = {
        "policy_number": "P1",
        "status": "Active",
        "active": True,
        "effective_date": "2026-03-01",
        "annualized_premium": 1000,
    }
    base.update(kw)
    return base


def led(**kw):
    base = {"policy_number": "P1", "reconciliation_status": "pending"}
    base.update(kw)
    return base


# --- status counts -----------------------------------------------------------

def test_status_counts_are_lowercased_and_sorted_by_frequency():
    rows = [led(reconciliation_status=s) for s in
            ["pending", "PENDING", "underpaid", "pending", "Overpaid"]]
    counts = surface.status_counts(rows)
    assert counts == {"pending": 3, "overpaid": 1, "underpaid": 1}
    assert list(counts)[0] == "pending"   # most frequent first


def test_status_counts_bucket_missing_status_as_unknown():
    assert surface.status_counts([{"reconciliation_status": None}]) == {"unknown": 1}


def test_status_counts_of_nothing_is_empty_not_an_error():
    assert surface.status_counts([]) == {}


# --- coverage buckets --------------------------------------------------------

def test_policy_already_on_the_surface_counts_as_in_ledger():
    c = surface.coverage([pol(policy_number="A")], {"A"}, since=SINCE)
    assert (c.active_policies, c.in_ledger) == (1, 1)
    assert c.missing_in_window == 0


def test_pre_floor_policy_is_a_deliberate_exclusion_not_a_gap():
    c = surface.coverage(
        [pol(policy_number="A", effective_date="2025-06-01", annualized_premium=5000)],
        set(), since=SINCE,
    )
    assert c.excluded_by_date_floor == 1
    assert c.excluded_premium == 5000
    assert c.missing_in_window == 0          # NOT reported as a gap


def test_in_window_policy_with_no_ledger_row_is_a_real_gap():
    c = surface.coverage(
        [pol(policy_number="A", effective_date="2026-04-01", annualized_premium=2500)],
        set(), since=SINCE,
    )
    assert c.missing_in_window == 1
    assert c.missing_in_window_premium == 2500
    assert c.excluded_by_date_floor == 0


def test_non_commissionable_status_is_its_own_bucket():
    c = surface.coverage([pol(policy_number="A", status="Renewing")], set(), since=SINCE)
    assert c.excluded_by_status == 1
    assert c.missing_in_window == 0


def test_renewed_is_commissionable():
    c = surface.coverage([pol(policy_number="A", status="Renewed")], set(), since=SINCE)
    assert c.excluded_by_status == 0
    assert c.missing_in_window == 1


def test_inactive_policies_are_not_counted_at_all():
    c = surface.coverage([pol(active=False)], set(), since=SINCE)
    assert c.active_policies == 0


def test_tombstoned_rows_are_skipped_and_tallied_separately():
    """The disabled importer's phantom rows must not inflate the book."""
    c = surface.coverage(
        [pol(status="Inactive: not in NowCerts 2026-07-21")], set(), since=SINCE,
    )
    assert c.active_policies == 0
    assert c.tombstoned_skipped == 1


def test_status_precedence_beats_the_date_floor():
    # A cancelled pre-2026 policy is excluded for being uncommissionable,
    # not for its date — otherwise the floor absorbs unrelated exclusions.
    c = surface.coverage(
        [pol(status="Cancelled", effective_date="2025-01-01")], set(), since=SINCE,
    )
    assert c.excluded_by_status == 1
    assert c.excluded_by_date_floor == 0


def test_ledger_membership_beats_every_exclusion():
    c = surface.coverage(
        [pol(policy_number="A", status="Cancelled", effective_date="2020-01-01")],
        {"A"}, since=SINCE,
    )
    assert c.in_ledger == 1
    assert c.excluded_by_status == 0


def test_missing_effective_date_is_treated_as_in_window():
    # No date can't mean "before the floor" — that would hide it silently.
    c = surface.coverage([pol(effective_date=None)], set(), since=SINCE)
    assert c.missing_in_window == 1


# --- the identity ------------------------------------------------------------

def test_every_active_policy_lands_in_exactly_one_bucket():
    policies = [
        pol(policy_number="A"),                                            # in ledger
        pol(policy_number="B", effective_date="2025-01-01"),               # floor
        pol(policy_number="C", status="Renewing"),                         # status
        pol(policy_number="D"),                                            # gap
        pol(policy_number="E", active=False),                              # not counted
        pol(policy_number="F", status="Inactive: not in NowCerts 2026-07-21"),
    ]
    c = surface.coverage(policies, {"A"}, since=SINCE)
    assert c.active_policies == 4
    assert (c.in_ledger, c.excluded_by_date_floor, c.excluded_by_status, c.missing_in_window) == (1, 1, 1, 1)
    assert c.balanced, "every active policy must be explained by exactly one bucket"


def test_balanced_is_false_when_the_maths_stops_adding_up():
    c = surface.Coverage(active_policies=10, in_ledger=3)
    assert not c.balanced


def test_as_dict_shape_is_stable_for_the_api():
    c = surface.coverage([pol(effective_date="2025-01-01")], set(), since=SINCE)
    d = c.as_dict()
    assert d["excluded_by_date_floor"] == {"policies": 1, "premium": 1000.0, "since": SINCE}
    assert d["balanced"] is True


# --- premium resolution ------------------------------------------------------

@pytest.mark.parametrize("policy,expected", [
    ({"annualized_premium": 500}, 500),
    ({"current_term_amount": 300}, 300),
    ({"premium_amount": 200}, 200),
    ({"annualized_premium": "", "premium_amount": 42}, 42),
    ({"annualized_premium": "junk", "premium_amount": 7}, 7),
    ({}, 0.0),
])
def test_premium_resolution_order(policy, expected):
    assert surface._premium(policy) == expected


# --- the floor is shared with the seeder -------------------------------------

def test_floor_reads_the_same_env_var_the_seeder_uses(monkeypatch):
    monkeypatch.setenv("HERMES_COMMISSION_SINCE", "2027-06-01")
    assert surface.commission_since() == "2027-06-01"


def test_floor_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("HERMES_COMMISSION_SINCE", raising=False)
    assert surface.commission_since() == surface.DEFAULT_SINCE


# --- overview ----------------------------------------------------------------

class FakeSupa:
    def __init__(self, ledger):
        self._ledger = ledger

    def select(self, table, **kw):
        assert table == surface.LEDGER_TABLE
        return list(self._ledger)


def test_overview_reports_counts_even_when_the_filter_matches_nothing(monkeypatch):
    """The exact bug: filter matches zero rows, but 108 rows exist."""
    ledger = [led(policy_number=f"P{i}", reconciliation_status="pending") for i in range(5)]
    monkeypatch.setattr(
        "hermes.ams.book.select_policies",
        lambda *a, **k: [pol(policy_number="P0")],
    )
    ov = surface.commission_overview(FakeSupa(ledger), status="reconciled")
    assert ov.rows == []                       # nothing is reconciled
    assert ov.counts_by_status == {"pending": 5}   # but five rows exist
    assert ov.total_ledger_rows == 5
    assert ov.as_dict()["count"] == 0


def test_overview_status_all_returns_everything(monkeypatch):
    ledger = [led(reconciliation_status="pending"), led(reconciliation_status="overpaid")]
    monkeypatch.setattr("hermes.ams.book.select_policies", lambda *a, **k: [])
    assert len(surface.commission_overview(FakeSupa(ledger), status="all").rows) == 2


def test_overview_survives_a_book_read_failure(monkeypatch):
    """Coverage is context — losing it must not blank the commissions view."""
    def boom(*a, **k):
        raise RuntimeError("AMS down")
    monkeypatch.setattr("hermes.ams.book.select_policies", boom)
    ov = surface.commission_overview(FakeSupa([led()]), status="all")
    assert len(ov.rows) == 1
    assert ov.coverage.active_policies == 0



# --- analytics rollups (#236) -----------------------------------------------

class _Supa:
    def __init__(self, rows):
        self._rows = rows

    def select(self, table, *, columns="*", params=None, limit=10000):
        assert table == surface.LEDGER_TABLE
        return [dict(r) for r in self._rows][:limit]


def test_analytics_rolls_up_by_carrier_and_lob():
    rows = [
        led(policy_number="A", carrier_name="Progressive", lob="Auto",
            gross_premium=1000, expected_commission=150, actual_commission=150,
            delta=0, reconciliation_status="reconciled"),
        led(policy_number="B", carrier_name="Progressive", lob="Auto",
            gross_premium=2000, expected_commission=300, actual_commission=250,
            delta=-50, reconciliation_status="underpaid"),
        led(policy_number="C", carrier_name="Next", lob="Home",
            gross_premium=5000, expected_commission=750, actual_commission=None,
            delta=None, reconciliation_status="pending"),
    ]
    out = surface.commission_analytics(_Supa(rows)).as_dict()

    prog = next(b for b in out["by_carrier"] if b["key"] == "Progressive")
    assert prog["policies"] == 2
    assert prog["gross_premium"] == 3000
    assert prog["expected_commission"] == 450
    assert prog["actual_commission"] == 400
    assert prog["delta"] == -50
    assert prog["statuses"] == {"reconciled": 1, "underpaid": 1}

    auto = next(b for b in out["by_lob"] if b["key"] == "Auto")
    assert auto["policies"] == 2 and auto["expected_commission"] == 450
    home = next(b for b in out["by_lob"] if b["key"] == "Home")
    assert home["policies"] == 1 and home["actual_commission"] == 0  # None -> 0

    assert out["totals"]["ledger_rows"] == 3
    assert out["totals"]["expected_commission"] == 1200
    assert out["totals"]["actual_commission"] == 400
    assert out["totals"]["counts_by_status"] == {"pending": 1, "reconciled": 1, "underpaid": 1}


def test_analytics_buckets_unknown_carrier_and_lob_visibly():
    rows = [led(policy_number="X", carrier_name="", lob=None,
                expected_commission=100, actual_commission=None)]
    out = surface.commission_analytics(_Supa(rows)).as_dict()
    assert out["by_carrier"][0]["key"] == "(unknown)"
    assert out["by_lob"][0]["key"] == "(unknown)"
    assert out["by_lob"][0]["actual_commission"] == 0  # None coerced, not summed as null


def test_analytics_sorts_carriers_by_expected_desc():
    rows = [
        led(policy_number="A", carrier_name="Small", expected_commission=10),
        led(policy_number="B", carrier_name="Big", expected_commission=1000),
    ]
    out = surface.commission_analytics(_Supa(rows)).as_dict()
    assert [b["key"] for b in out["by_carrier"]] == ["Big", "Small"]


def test_analytics_a_read_failure_is_honest_not_a_crash():
    class BoomSupa:
        def select(self, table, *, columns="*", params=None, limit=10000):
            raise RuntimeError("supabase down")
    out = surface.commission_analytics(BoomSupa()).as_dict()
    assert out["by_carrier"] == [] and out["by_lob"] == []
    assert out["totals"]["ledger_rows"] == 0
