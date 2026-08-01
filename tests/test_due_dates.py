"""One rule for due dates, and one rule for case numbers."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from hermes_core.due_dates import (
    AGENCY_TZ,
    agency_today,
    due_in_days,
    end_of_business,
    normalize_due,
)
from hermes.renewals import cases as C


def _et(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(AGENCY_TZ)


# --- normalize_due ------------------------------------------------------------
def test_a_bare_date_becomes_close_of_business():
    assert normalize_due("2026-08-15") == "2026-08-15T17:00:00-04:00"


def test_midnight_utc_keeps_its_own_day():
    """The live bug: midnight UTC is 8pm the day BEFORE in Eastern time, so a case
    due the 24th was displaying as due the 23rd."""
    assert normalize_due("2026-07-24T00:00:00+00:00") == "2026-07-24T17:00:00-04:00"


def test_midnight_local_is_a_date_not_a_time():
    assert normalize_due("2026-08-15T00:00:00") == "2026-08-15T17:00:00-04:00"


def test_a_real_time_of_day_is_kept():
    """Only midnight is overridden — a time someone actually chose survives."""
    assert normalize_due("2026-08-15T09:30:00-04:00") == "2026-08-15T09:30:00-04:00"


def test_a_naive_timestamp_is_read_as_agency_time():
    """Not UTC: the container's clock is not where the agency works."""
    assert normalize_due("2026-08-15T09:30:00") == "2026-08-15T09:30:00-04:00"


def test_winter_dates_carry_the_winter_offset():
    assert normalize_due("2026-01-15") == "2026-01-15T17:00:00-05:00"


def test_date_and_datetime_objects_are_accepted():
    assert normalize_due(date(2026, 8, 15)) == "2026-08-15T17:00:00-04:00"
    assert normalize_due(datetime(2026, 8, 15, 9, 30, tzinfo=timezone.utc)) == "2026-08-15T05:30:00-04:00"


def test_no_due_date_is_allowed():
    assert normalize_due(None) is None
    assert normalize_due("") is None
    assert normalize_due("   ") is None


def test_junk_is_refused_at_the_edge():
    """Better a 400 than a row nobody can explain later."""
    with pytest.raises(ValueError):
        normalize_due("next tuesday")


def test_normalizing_twice_changes_nothing():
    once = normalize_due("2026-08-15")
    assert normalize_due(once) == once


def test_the_due_date_survives_a_round_trip_to_utc():
    """However it is stored, it has to read back as the same DAY in the office."""
    stored = datetime.fromisoformat(normalize_due("2026-08-15")).astimezone(timezone.utc)
    assert stored.astimezone(AGENCY_TZ).date() == date(2026, 8, 15)


# --- due_in_days --------------------------------------------------------------
def test_due_in_days_counts_whole_days_from_today():
    out = _et(due_in_days(3))
    assert out.date() == agency_today() + __import__("datetime").timedelta(days=3)
    assert (out.hour, out.minute) == (17, 0)


def test_due_in_days_is_not_moved_by_the_time_of_day():
    """`utcnow() + n` opened at 8pm ET landed on the wrong date outright, because
    8pm ET is already tomorrow in UTC."""
    late = datetime(2026, 7, 23, 23, 30, tzinfo=timezone.utc)   # 7:30pm ET on the 23rd
    assert _et(due_in_days(1, now=late)).date() == date(2026, 7, 24)


def test_due_in_zero_days_is_today():
    assert _et(due_in_days(0)).date() == agency_today()


def test_end_of_business_is_five_pm_agency_time():
    assert end_of_business(date(2026, 8, 15)).hour == 17


# --- case numbers -------------------------------------------------------------
def test_a_case_with_an_identity_is_numbered_from_it():
    assert C.case_number("renewal", identity="9300232193", on="2026-10-29") == "REN-9300232193-20261029"


def test_the_same_identity_always_gives_the_same_number():
    """This is what lets the renewal desk be re-run without opening a second case."""
    a = C.case_number("renewal", identity="POL-1", on="2026-10-29")
    b = C.case_number("renewal", identity="POL-1", on="2026-10-29")
    assert a == b


def test_the_renewal_helper_still_matches_what_is_live():
    """A live case is numbered REN-9300232193-20261029; the layout is not free to move."""
    assert C.renewal_case_number("9300232193", "lineage-x", "2026-10-29") == "REN-9300232193-20261029"


def test_a_case_without_an_identity_is_dated_and_random():
    a = C.case_number("service")
    b = C.case_number("service")
    assert a != b
    assert a.startswith("SER-") and len(a.split("-")) == 3


def test_the_number_is_dated_in_agency_time_not_utc():
    """Opened at 8pm ET, `utcnow()` would stamp it with tomorrow's date."""
    late = datetime(2026, 7, 23, 23, 30, tzinfo=timezone.utc)
    assert C.case_number("service", now=late).split("-")[1] == "20260723"


def test_identity_is_slugged_so_a_policy_number_is_safe_in_the_id():
    assert C.case_number("renewal", identity="POL 123/AB", on="2026-01-01") == "REN-POL123AB-20260101"


def test_an_unknown_case_type_still_gets_a_number():
    assert C.case_number(None).startswith("CAS-")


def test_the_prefix_matches_the_live_numbers():
    assert C.case_number("marketing").startswith("MAR-")
    assert C.case_number("service").startswith("SER-")
