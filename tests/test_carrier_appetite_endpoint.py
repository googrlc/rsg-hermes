"""Regression tests for GET /api/carriers.

The endpoint shipped selecting columns that do not exist on `carrier_appetite`
(`carrier`, `state`, `line_of_business`, `naics_code`, `commission_percent`, …).
PostgREST rejects the whole request when a selected column is unknown, the
Supabase client raises, and FastAPI turns that into a 500 — so the Carrier Desk
answered "who writes this?" with an Internal Server Error on every call,
including with no filters at all. These tests pin the column list to the real
table and cover the array-aware state/code filtering.
"""
from __future__ import annotations

import pytest

from hermes import carriers as CA

# Real `carrier_appetite` columns (public schema, Supabase wibscqhkvpijzqbhjphg).
LIVE_COLUMNS = {
    "id", "carrier_name", "lob", "appetite_level", "min_premium", "max_premium",
    "states_approved", "key_requirements", "exclusions", "notes", "effective_date",
    "active", "created_at", "updated_at", "carrier_id", "details", "class_codes",
    "source", "source_document", "confidence", "updated_by",
}


def test_appetite_columns_all_exist_on_the_table():
    """Every selected column must exist — one that doesn't 500s the whole endpoint."""
    selected = {c.strip() for c in CA.APPETITE_COLUMNS.split(",") if c.strip()}
    assert selected <= LIVE_COLUMNS, f"columns not on carrier_appetite: {selected - LIVE_COLUMNS}"


def test_appetite_columns_carry_the_fields_a_desk_needs():
    selected = {c.strip() for c in CA.APPETITE_COLUMNS.split(",")}
    for needed in ("carrier_name", "lob", "appetite_level", "states_approved",
                   "exclusions", "key_requirements", "class_codes", "confidence"):
        assert needed in selected


# --- state filtering (text[], "ALL" == nationwide) ---
GA = {"carrier_name": "GA-Only", "states_approved": ["GA"]}
ALL = {"carrier_name": "Nationwide", "states_approved": ["ALL"]}
FL = {"carrier_name": "FL-Only", "states_approved": ["FL"]}
NONE = {"carrier_name": "Unscoped", "states_approved": None}


def test_all_scope_survives_a_state_filter():
    """The broadest appointments are scoped ["ALL"] — dropping them would hide
    exactly the carriers most likely to write the risk."""
    out = CA.filter_by_state([GA, ALL, FL], "GA")
    names = {r["carrier_name"] for r in out}
    assert names == {"GA-Only", "Nationwide"}


def test_blank_state_is_a_noop():
    rows = [GA, ALL, FL]
    assert CA.filter_by_state(rows, None) == rows
    assert CA.filter_by_state(rows, "  ") == rows


def test_state_match_is_case_insensitive():
    assert CA.filter_by_state([GA], "ga") == [GA]


def test_null_states_approved_does_not_crash_and_does_not_match():
    assert CA.filter_by_state([NONE], "GA") == []


# --- class-code filtering ---
@pytest.mark.parametrize("stored,query,expected", [
    (["91341"], "91341", True),
    (["91341"], "ISO 91341", True),      # how a producer types it
    (["91-341"], "91341", True),         # how it got stored
    (["91341"], "12374", False),
    ([], "12374", False),                # no codes recorded != writes everything
    (["91341"], None, True),             # blank query matches everything
])
def test_matches_code(stored, query, expected):
    assert CA.matches_code({"class_codes": stored}, query) is expected


def test_empty_class_codes_is_not_treated_as_universal_appetite():
    """A row with no codes must not be reported as eligible for a code it never
    listed — that would manufacture appetite the carrier never confirmed."""
    rows = [{"carrier_name": "No Codes", "class_codes": []}]
    assert [r for r in rows if CA.matches_code(r, "12374")] == []
