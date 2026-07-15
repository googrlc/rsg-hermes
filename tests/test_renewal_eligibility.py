"""Exhaustive tests for the centralized renewal-eligibility rule."""

from __future__ import annotations

from datetime import date, timedelta

from hermes.renewals import eligibility as elig
from hermes.renewals.eligibility import LineageContext

TODAY = date(2026, 7, 15)


def pol(**kw):
    base = {
        "policy_number": "POL1",
        "nowcerts_insured_guid": "ins-1",
        "status": "Active",
        "line_of_business": "General Liability",
        "effective_date": (TODAY - timedelta(days=100)).isoformat(),
        "expiration_date": (TODAY + timedelta(days=60)).isoformat(),
    }
    base.update(kw)
    return base


def ev(policy, *, insured_active=True, premium=1000.0, lineage=None):
    return elig.evaluate(policy, insured_active=insured_active, today=TODAY,
                         account_active_premium=premium, lineage=lineage)


# --- normalization / helpers --------------------------------------------------
def test_normalize_status_crosswalk():
    assert elig.normalize_status("in force") == "Active"
    assert elig.normalize_status("BOUND") == "Active"
    assert elig.normalize_status("renewal pending") == "Up for Renewal"
    assert elig.normalize_status("in renewal") == "Renewing"
    assert elig.normalize_status("flat-cancel") == "Flat Cancel"
    assert elig.normalize_status("nonrenewed") == "Non-Renewed"
    assert elig.normalize_status("lapsed") == "Lapsed"
    assert elig.normalize_status("wat") == ""


def test_next_aug_1():
    assert elig.next_aug_1(date(2026, 7, 15)) == date(2026, 8, 1)
    assert elig.next_aug_1(date(2026, 8, 1)) == date(2026, 8, 1)
    assert elig.next_aug_1(date(2026, 9, 1)) == date(2027, 8, 1)


def test_derive_lineage_id_uses_root():
    p = pol(policy_number="NXTV7VDYCL-03-GL")
    assert elig.derive_lineage_id(p, root_policy_number="NXTV7VDYCL-01-GL").endswith(":NXTV7VDYCL-01-GL")
    assert elig.derive_lineage_id(p).endswith(":NXTV7VDYCL-03-GL")  # self when no root


# --- branch A: current term ---------------------------------------------------
def test_current_term_eligible_and_in_queue():
    r = ev(pol(expiration_date=(TODAY + timedelta(days=60)).isoformat()))  # commercial, 60<=90
    assert r.state == elig.STATE_ELIGIBLE
    assert r.branch == elig.BRANCH_CURRENT_TERM
    assert r.event_date == TODAY + timedelta(days=60)
    assert r.in_working_queue is True


def test_current_term_in_pool_but_not_working_queue():
    r = ev(pol(expiration_date=(TODAY + timedelta(days=100)).isoformat()))  # commercial, 100>90
    assert r.state == elig.STATE_ELIGIBLE
    assert r.in_working_queue is False


def test_current_term_outside_120_window_excluded():
    r = ev(pol(expiration_date=(TODAY + timedelta(days=150)).isoformat()))
    assert r.state == elig.STATE_EXCLUDED
    assert "outside" in r.reason


def test_current_term_with_staged_successor_excluded():
    lin = LineageContext(lineage_id="L", has_valid_successor=True)
    r = ev(pol(), lineage=lin)
    assert r.state == elig.STATE_EXCLUDED
    assert "successor" in r.reason


# --- branch B: staged next term ----------------------------------------------
def test_staged_next_term_eligible():
    lin = LineageContext(lineage_id="L", follows_current_term=True, predecessor_policy_number="POL0")
    r = ev(pol(status="Renewing",
               effective_date=(TODAY + timedelta(days=20)).isoformat(),
               expiration_date=(TODAY + timedelta(days=385)).isoformat()), lineage=lin)
    assert r.state == elig.STATE_ELIGIBLE
    assert r.branch == elig.BRANCH_STAGED_NEXT_TERM
    assert r.event_date == TODAY + timedelta(days=20)
    assert r.predecessor_policy_number == "POL0"


def test_staged_without_predecessor_needs_verification():
    r = ev(pol(status="Up for Renewal",
               effective_date=(TODAY + timedelta(days=20)).isoformat()))  # self_lineage, follows=False
    assert r.state == elig.STATE_NEEDS_VERIFICATION
    assert "follow" in r.reason


def test_staged_without_future_effective_needs_verification():
    r = ev(pol(status="Renewing",
               effective_date=(TODAY - timedelta(days=5)).isoformat(),
               expiration_date=(TODAY + timedelta(days=360)).isoformat()))
    assert r.state == elig.STATE_NEEDS_VERIFICATION


# --- exclusions ---------------------------------------------------------------
def test_dead_statuses_excluded():
    for s in ["Cancelled", "Expired", "Flat Cancel", "Non-Renewed", "Lapsed"]:
        r = ev(pol(status=s, effective_date=(TODAY - timedelta(days=400)).isoformat(),
                   expiration_date=(TODAY - timedelta(days=30)).isoformat()))
        assert r.state == elig.STATE_EXCLUDED, s


def test_dead_status_with_current_dates_needs_verification():
    # Dirty: status Cancelled/Expired but the dates say it is still in force.
    r = ev(pol(status="Expired",
               effective_date=(TODAY - timedelta(days=100)).isoformat(),
               expiration_date=(TODAY + timedelta(days=60)).isoformat()))
    assert r.state == elig.STATE_NEEDS_VERIFICATION


def test_superseded_excluded():
    for s in ["Renewed", "Rewritten"]:
        assert ev(pol(status=s)).state == elig.STATE_EXCLUDED


def test_inactive_insured_excluded_even_if_perfect_current_term():
    r = ev(pol(), insured_active=False)
    assert r.state == elig.STATE_EXCLUDED
    assert "insured" in r.reason


def test_no_expiration_excluded():
    r = ev(pol(status="Up for Renewal", effective_date=None, expiration_date=None))
    assert r.state == elig.STATE_EXCLUDED
    assert "trustworthy" in r.reason


def test_pending_cancel_routes_to_verification():
    r = ev(pol(status="Pending Cancel"))
    assert r.state == elig.STATE_NEEDS_VERIFICATION


def test_active_but_expired_dates_needs_verification():
    r = ev(pol(status="Active",
               effective_date=(TODAY - timedelta(days=400)).isoformat(),
               expiration_date=(TODAY - timedelta(days=5)).isoformat()))
    assert r.state == elig.STATE_NEEDS_VERIFICATION


# --- medicare -----------------------------------------------------------------
def test_medicare_mapd_eligible_annual_aug1():
    r = ev(pol(line_of_business="MAPD", status="Active"))
    assert r.state == elig.STATE_ELIGIBLE
    assert r.branch == elig.BRANCH_MEDICARE_ANNUAL
    assert r.event_date == date(2026, 8, 1)
    assert r.segment == elig.SEGMENT_MEDICARE
    assert r.in_working_queue is False  # today 7/15 < 8/1


def test_medicare_in_working_queue_on_aug1():
    r = elig.evaluate(pol(line_of_business="Medicare Advantage"),
                      insured_active=True, today=date(2026, 8, 1))
    assert r.state == elig.STATE_ELIGIBLE
    assert r.in_working_queue is True


def test_medicare_outside_window_excluded():
    # From April, next Aug 1 is >120 days out.
    r = elig.evaluate(pol(line_of_business="Medigap"),
                      insured_active=True, today=date(2026, 3, 1))
    assert r.state == elig.STATE_EXCLUDED


# --- segment / threshold integration -----------------------------------------
def test_personal_auto_uses_30day_entry():
    p = pol(line_of_business="Personal Auto",
            effective_date=(TODAY - timedelta(days=150)).isoformat(),
            expiration_date=(TODAY + timedelta(days=30)).isoformat())  # 6mo term
    r = ev(p, premium=None)
    assert r.segment == elig.cadence.cc.SEGMENT_AUTO_6MO
    assert r.in_working_queue is True  # 30 <= 30
    # 45 days out -> in pool, not yet in the working queue
    r2 = ev(pol(line_of_business="Personal Auto",
                effective_date=(TODAY - timedelta(days=135)).isoformat(),
                expiration_date=(TODAY + timedelta(days=45)).isoformat()), premium=None)
    assert r2.in_working_queue is False


def test_benefits_uses_120day_entry():
    r = ev(pol(line_of_business="Group Health",
               expiration_date=(TODAY + timedelta(days=110)).isoformat()))
    assert r.segment == elig.cadence.cc.SEGMENT_BENEFITS
    assert r.in_working_queue is True  # entry = event-120, always reached inside the pool


def test_commercial_small_vs_mid_by_account_premium():
    small = ev(pol(line_of_business="General Liability"), premium=1000.0)
    mid = ev(pol(line_of_business="General Liability"), premium=20000.0)
    assert small.segment == elig.cadence.cc.SEGMENT_COMMERCIAL_SMALL
    assert mid.segment == elig.cadence.cc.SEGMENT_COMMERCIAL_MID
