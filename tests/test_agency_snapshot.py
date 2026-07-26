"""Tests for the agency snapshot writer.

The retention rule is the part worth pinning down: it decides whether the agency
believes it is at 54% or 66%, so each of the three "this term continued" signals
gets its own test, plus the cases that must NOT count as churn.
"""

from __future__ import annotations

from datetime import date

import pytest

from hermes.jobs import agency_snapshot as snap

TODAY = date(2026, 7, 26)


def policy(**kw):
    base = {
        "policy_guid": kw.pop("guid", None) or f"g-{kw.get('policy_number', 'x')}-{kw.get('effective_date', '')}",
        "policy_number": "P-1",
        "renewed_policy": None,
        "lines_of_business": "Commercial Auto",
        "status": "Expired",
        "active": False,
        "effective_date": "2025-01-01",
        "expiration_date": "2026-01-01",
        "annualized_premium": 1000,
    }
    base.update(kw)
    return base


# --- premium resolution ------------------------------------------------------

def test_premium_falls_through_the_field_order():
    assert snap.policy_premium({"annualized_premium": 500}) == 500
    assert snap.policy_premium({"current_term_amount": 300}) == 300
    assert snap.policy_premium({"premium_amount": 200}) == 200
    # empty string is not a premium
    assert snap.policy_premium({"annualized_premium": "", "premium_amount": 42}) == 42
    assert snap.policy_premium({}) == 0.0


def test_premium_survives_garbage():
    assert snap.policy_premium({"annualized_premium": "not-a-number", "premium_amount": 7}) == 7


# --- LOB bucketing -----------------------------------------------------------

@pytest.mark.parametrize(
    "lob,bucket",
    [
        ("Commercial Auto", snap.BUCKET_COMMERCIAL_AUTO),
        ("Garage and Dealers", snap.BUCKET_COMMERCIAL_AUTO),
        ("Worker's Compensation", snap.BUCKET_WORKERS_COMP),
        ("General Liability", snap.BUCKET_GL_BOP),
        ("Business Owners", snap.BUCKET_GL_BOP),
        ("Commercial Package", snap.BUCKET_GL_BOP),
        ("Personal Auto", snap.BUCKET_PERSONAL),
        ("Homeowners", snap.BUCKET_PERSONAL),
        ("Motorcycle", snap.BUCKET_PERSONAL),
        ("Life", snap.BUCKET_OTHER),
        ("", snap.BUCKET_OTHER),
        (None, snap.BUCKET_OTHER),
    ],
)
def test_lob_buckets(lob, bucket):
    assert snap.lob_bucket(lob) == bucket


def test_lob_bucketing_tolerates_the_typos_in_the_book():
    # These values are really in canonical_policies.
    assert snap.lob_bucket("personsl auto") == snap.BUCKET_PERSONAL
    assert snap.lob_bucket("PersonalAuto") == snap.BUCKET_PERSONAL


def test_commercial_auto_wins_over_the_bare_personal_hint():
    # "Commercial Auto" must not fall through to personal lines.
    assert snap.lob_bucket("Commercial Auto") == snap.BUCKET_COMMERCIAL_AUTO


# --- retention ---------------------------------------------------------------

def test_status_renewed_counts_as_retained():
    rows = [policy(status="Renewed", expiration_date="2026-03-01")]
    r = snap.compute_retention(rows, today=TODAY)
    assert (r.denominator, r.retained) == (1, 1)
    assert r.logo_rate == 100.0


def test_successor_pointer_counts_as_retained():
    expiring = policy(guid="a", policy_number="NXT-02", expiration_date="2026-03-01")
    successor = policy(
        guid="b", policy_number="NXT-03", renewed_policy="NXT-02",
        effective_date="2026-03-01", expiration_date="2027-03-01",
    )
    r = snap.compute_retention([expiring, successor], today=TODAY)
    assert r.retained == 1


def test_same_policy_number_later_term_counts_as_retained():
    # Renewals routinely keep the number; 135 of 294 numbers carry multiple terms.
    old = policy(guid="a", policy_number="OF29", effective_date="2025-03-01", expiration_date="2026-03-01")
    new = policy(guid="b", policy_number="OF29", effective_date="2026-03-01", expiration_date="2027-03-01")
    r = snap.compute_retention([old, new], today=TODAY)
    assert r.denominator == 1  # only the expired term is graded
    assert r.retained == 1


def test_successor_within_the_grace_window_still_counts():
    # Carriers backdate; a successor starting 30 days early is the same renewal.
    old = policy(guid="a", policy_number="P9", expiration_date="2026-03-01")
    new = policy(guid="b", policy_number="P9", effective_date="2026-02-01", expiration_date="2027-02-01")
    r = snap.compute_retention([old, new], today=TODAY)
    assert r.retained == 1


def test_successor_long_before_expiry_does_not_count():
    # An unrelated earlier term must not be mistaken for the renewal.
    old = policy(guid="a", policy_number="P9", effective_date="2025-03-01", expiration_date="2026-03-01")
    older = policy(guid="b", policy_number="P9", effective_date="2024-03-01", expiration_date="2025-03-01")
    r = snap.compute_retention([old, older], today=TODAY)
    # `old` expired in-window with no successor -> lost. `older` is out of window.
    assert (r.denominator, r.retained) == (1, 0)


def test_cancelled_with_no_successor_is_churn():
    rows = [policy(status="Cancelled", expiration_date="2026-02-01")]
    r = snap.compute_retention(rows, today=TODAY)
    assert (r.denominator, r.retained) == (1, 0)
    assert r.logo_rate == 0.0


def test_terms_outside_the_window_are_not_graded():
    rows = [
        policy(guid="old", expiration_date="2024-01-01"),      # too old
        policy(guid="future", expiration_date="2027-01-01"),   # hasn't happened
    ]
    r = snap.compute_retention(rows, today=TODAY)
    assert r.denominator == 0
    assert r.logo_rate is None


def test_tombstoned_terms_are_excluded_not_counted_as_lost():
    """The importer bug must not manufacture churn."""
    rows = [
        policy(guid="real", expiration_date="2026-02-01", status="Renewed"),
        policy(guid="ghost", expiration_date="2026-02-01",
               status="Inactive: not in NowCerts 2026-07-21"),
    ]
    r = snap.compute_retention(rows, today=TODAY)
    assert r.denominator == 1
    assert r.excluded_tombstoned == 1
    assert r.logo_rate == 100.0


def test_premium_weighted_differs_from_logo():
    rows = [
        policy(guid="a", policy_number="BIG", status="Renewed",
               expiration_date="2026-02-01", annualized_premium=9000),
        policy(guid="b", policy_number="SMALL", status="Cancelled",
               expiration_date="2026-02-01", annualized_premium=1000),
    ]
    r = snap.compute_retention(rows, today=TODAY)
    assert r.logo_rate == 50.0          # kept 1 of 2 policies
    assert r.premium_rate == 90.0       # but 90% of the money
    assert r.lost == 1
    assert r.lost_premium == 1000


def test_a_row_is_never_its_own_successor():
    # 88 rows self-reference (renewed_policy == policy_number). That must not
    # make a lapsed policy look renewed.
    rows = [policy(guid="a", policy_number="SELF", renewed_policy="SELF",
                   status="Cancelled", expiration_date="2026-02-01")]
    r = snap.compute_retention(rows, today=TODAY)
    assert r.retained == 0


def test_missing_expiration_is_skipped():
    r = snap.compute_retention([policy(expiration_date=None)], today=TODAY)
    assert r.denominator == 0


# --- book --------------------------------------------------------------------

def test_book_counts_only_active_and_splits_by_lob():
    rows = [
        policy(guid="1", active=True, lines_of_business="Commercial Auto", annualized_premium=1000),
        policy(guid="2", active=True, lines_of_business="Personal Auto", annualized_premium=500),
        policy(guid="3", active=False, lines_of_business="Personal Auto", annualized_premium=999),
    ]
    b = snap.compute_book(rows, client_count=7)
    assert b.active_policy_count == 2
    assert b.active_premium == 1500
    assert b.total_policy_count == 3
    assert b.client_count == 7
    assert b.lob_premium[snap.BUCKET_COMMERCIAL_AUTO] == 1000
    assert b.lob_premium[snap.BUCKET_PERSONAL] == 500


def test_tombstoned_rows_are_excluded_from_the_book_and_reported():
    rows = [
        policy(guid="1", active=True, annualized_premium=1000),
        policy(guid="2", active=True, annualized_premium=400,
               status="Inactive: not in NowCerts 2026-07-21"),
    ]
    b = snap.compute_book(rows)
    assert b.active_premium == 1000
    assert b.tombstoned_count == 1
    assert b.tombstoned_premium == 400


# --- snapshot assembly -------------------------------------------------------

def test_build_snapshot_shape_and_headline():
    rows = [
        policy(guid="a", policy_number="K", status="Renewed", active=True,
               expiration_date="2026-02-01", annualized_premium=8000),
        policy(guid="b", policy_number="L", status="Cancelled",
               expiration_date="2026-02-01", annualized_premium=2000),
    ]
    row = snap.build_snapshot(rows, today=TODAY, client_count=3, live_reads=True)
    assert row["snapshot_date"] == "2026-07-26"
    assert row["policy_count"] == 1
    assert row["active_premium"] == 8000
    assert row["client_count"] == 3
    assert row["retention_rate"] == 80.0     # premium-weighted is the headline
    assert row["source"] == "auto"
    assert "logo" in row["notes"]            # logo rate rides along, never conflated
    assert all(b in row for b in snap.BUCKETS)


def test_snapshot_warns_when_reading_the_mirror():
    row = snap.build_snapshot([], today=TODAY, live_reads=False)
    assert "HERMES_AMS_LIVE_READS" in row["notes"]


def test_snapshot_does_not_warn_on_live_reads():
    row = snap.build_snapshot([], today=TODAY, live_reads=True)
    assert "HERMES_AMS_LIVE_READS" not in row["notes"]
    assert "live AMS" in row["notes"]


def test_deltas_are_computed_against_the_prior_snapshot():
    rows = [policy(guid="a", active=True, annualized_premium=1000, expiration_date="2027-01-01")]
    prior = {"active_premium": "800", "policy_count": 3, "retention_rate": "54.92",
             "snapshot_date": "2026-03-31"}
    row = snap.build_snapshot(rows, today=TODAY, prior=prior, live_reads=True)
    assert row["delta_premium"] == 200.0
    assert row["delta_policies"] == -2
    assert row["delta_retention"] is None   # no graded terms -> no rate -> no delta


def test_no_prior_snapshot_means_no_deltas():
    row = snap.build_snapshot([], today=TODAY, prior=None, live_reads=True)
    assert "delta_premium" not in row


def test_prior_with_junk_values_does_not_explode():
    row = snap.build_snapshot([], today=TODAY, live_reads=True,
                              prior={"active_premium": "n/a", "policy_count": "?"})
    assert row["delta_premium"] is None
    assert "delta_policies" not in row


# --- the real numbers --------------------------------------------------------

def test_retention_matches_the_shape_measured_against_production():
    """Guards the rule against silent drift.

    Measured live 2026-07-26: 238 graded terms, 148 retained -> 62.18% logo,
    66.66% premium-weighted. This fixture is a miniature with the same signal
    mix (status / pointer / same-number) to catch a regression in any one leg.
    """
    rows = [
        policy(guid="s", policy_number="S1", status="Renewed",
               expiration_date="2026-01-15", annualized_premium=1000),
        policy(guid="p1", policy_number="P1", expiration_date="2026-02-15", annualized_premium=1000),
        policy(guid="p2", policy_number="P2", renewed_policy="P1",
               effective_date="2026-02-15", expiration_date="2027-02-15", annualized_premium=1000),
        policy(guid="n1", policy_number="N1", effective_date="2025-03-15",
               expiration_date="2026-03-15", annualized_premium=1000),
        policy(guid="n2", policy_number="N1", effective_date="2026-03-15",
               expiration_date="2027-03-15", annualized_premium=1000),
        policy(guid="x", policy_number="X1", status="Cancelled",
               expiration_date="2026-04-15", annualized_premium=1000),
    ]
    r = snap.compute_retention(rows, today=TODAY)
    assert r.denominator == 4          # S1, P1, N1(old), X1
    assert r.retained == 3             # all but X1
    assert r.logo_rate == 75.0
