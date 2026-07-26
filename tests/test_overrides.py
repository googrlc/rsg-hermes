"""Tests for override resolution and the three-way reconcile.

The branch that matters is CONFLICT. Retiring an override just because the
source changed would silently discard a human correction and put a wrong number
back on a money surface. Each of the three outcomes gets explicit coverage, as
does the value-comparison sloppiness that would otherwise fake a conflict.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from hermes.overrides import core as ov

E, K, F = "commission_ledger", "P1", "gross_premium"


def make(override_value=1000, original_value=0, status=ov.STATUS_ACTIVE):
    return ov.Override(
        entity_type=E, entity_key=K, field_name=F,
        override_value=override_value, original_value=original_value,
        status=status, approved_by="lamar@risksolutionsgroup.net",
    )


# --- value comparison --------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    (535.65, "535.65"),
    ("535.65", Decimal("535.6500")),
    (1000, 1000.0),
    ("$1,000.00", 1000),
    (" ACTIVE ", "active"),
    (None, None),
    (True, True),
])
def test_values_that_are_the_same(a, b):
    assert ov.same_value(a, b)


@pytest.mark.parametrize("a,b", [
    (535.65, 535.70),
    (None, 0),          # absent is NOT zero — the bug that invents premium
    (0, None),
    ("active", "cancelled"),
    (None, ""),
])
def test_values_that_differ(a, b):
    assert not ov.same_value(a, b)


def test_money_tolerates_a_cent_of_rounding():
    assert ov.same_value(100.00, 100.005)
    assert not ov.same_value(100.00, 100.02)


def test_unparseable_strings_fall_back_to_text_comparison():
    assert ov.same_value("N/A", "n/a")
    assert not ov.same_value("N/A", "none")


# --- resolve -----------------------------------------------------------------

def test_active_override_replaces_the_source():
    assert ov.resolve(0, make(override_value=1000)) == 1000


def test_no_override_returns_the_source():
    assert ov.resolve(42, None) == 42


@pytest.mark.parametrize("status", [ov.STATUS_RETIRED, ov.STATUS_CONFLICTED])
def test_inactive_override_never_changes_what_is_shown(status):
    """A retired or conflicted correction is history, not truth."""
    assert ov.resolve(42, make(override_value=1000, status=status)) == 42


# --- the three-way reconcile -------------------------------------------------

def test_source_catches_up_retires_the_override():
    r = ov.reconcile(1000, make(override_value=1000, original_value=0))
    assert r.action == ov.ACTION_RETIRE
    assert r.retires
    assert r.reason == ov.RETIRED_AMS_MATCHED


def test_source_unchanged_keeps_overriding():
    r = ov.reconcile(0, make(override_value=1000, original_value=0))
    assert r.action == ov.ACTION_KEEP
    assert not r.retires and not r.conflicts


def test_source_moved_somewhere_else_is_a_conflict_not_a_retirement():
    """The whole point: don't discard a correction because something else moved."""
    r = ov.reconcile(750, make(override_value=1000, original_value=0))
    assert r.action == ov.ACTION_CONFLICT
    assert r.conflicts
    assert r.source_value == 750


def test_retirement_tolerates_type_drift_from_the_source():
    # AMS hands back a string; the override holds a float. Still caught up.
    r = ov.reconcile("1000.00", make(override_value=1000.0, original_value=0))
    assert r.action == ov.ACTION_RETIRE


def test_reconcile_never_mutates_the_override():
    o = make()
    ov.reconcile(999, o)
    assert o.status == ov.STATUS_ACTIVE


def test_inactive_override_is_left_alone():
    r = ov.reconcile(1, make(status=ov.STATUS_RETIRED))
    assert r.action == ov.ACTION_KEEP


def test_null_original_with_matching_source_still_retires():
    r = ov.reconcile(1000, make(override_value=1000, original_value=None))
    assert r.action == ov.ACTION_RETIRE


def test_source_going_null_when_original_was_null_keeps():
    r = ov.reconcile(None, make(override_value=1000, original_value=None))
    assert r.action == ov.ACTION_KEEP


# --- indexing ----------------------------------------------------------------

def test_index_keeps_only_active_rows():
    rows = [
        {"entity_type": E, "entity_key": "P1", "field_name": F,
         "override_value": 1, "status": "active"},
        {"entity_type": E, "entity_key": "P2", "field_name": F,
         "override_value": 2, "status": "retired"},
    ]
    idx = ov.index_overrides(rows)
    assert list(idx) == [(E, "P1", F)]


def test_index_trims_the_key():
    idx = ov.index_overrides([
        {"entity_type": E, "entity_key": "  P1 ", "field_name": F,
         "override_value": 1, "status": "active"},
    ])
    assert (E, "P1", F) in idx


# --- applying to records -----------------------------------------------------

def test_apply_replaces_the_value_and_records_what_it_replaced():
    records = [{"policy_number": "P1", "gross_premium": 0, "client_name": "Acme"}]
    idx = ov.index_overrides([
        {"entity_type": E, "entity_key": "P1", "field_name": "gross_premium",
         "override_value": 1000, "original_value": 0, "status": "active"},
    ])
    out = ov.apply_overrides(records, idx, entity_type=E, key_field="policy_number")
    assert out[0]["gross_premium"] == 1000
    assert out[0]["_overridden"] == {"gross_premium": 0}
    assert out[0]["client_name"] == "Acme"


def test_apply_does_not_mutate_the_input():
    records = [{"policy_number": "P1", "gross_premium": 0}]
    idx = ov.index_overrides([
        {"entity_type": E, "entity_key": "P1", "field_name": "gross_premium",
         "override_value": 1000, "status": "active"},
    ])
    ov.apply_overrides(records, idx, entity_type=E, key_field="policy_number")
    assert records[0]["gross_premium"] == 0


def test_untouched_records_carry_no_overridden_marker():
    records = [{"policy_number": "P2", "gross_premium": 5}]
    idx = ov.index_overrides([
        {"entity_type": E, "entity_key": "P1", "field_name": "gross_premium",
         "override_value": 1000, "status": "active"},
    ])
    out = ov.apply_overrides(records, idx, entity_type=E, key_field="policy_number")
    assert "_overridden" not in out[0]


def test_override_on_a_field_the_source_does_not_carry_is_still_applied():
    records = [{"policy_number": "P1"}]
    idx = ov.index_overrides([
        {"entity_type": E, "entity_key": "P1", "field_name": "note",
         "override_value": "checked", "status": "active"},
    ])
    out = ov.apply_overrides(records, idx, entity_type=E, key_field="policy_number")
    assert out[0]["note"] == "checked"
    assert out[0]["_overridden"] == {"note": None}


def test_overrides_for_a_different_entity_type_do_not_leak():
    records = [{"policy_number": "P1", "gross_premium": 0}]
    idx = ov.index_overrides([
        {"entity_type": "canonical_policies", "entity_key": "P1",
         "field_name": "gross_premium", "override_value": 999, "status": "active"},
    ])
    out = ov.apply_overrides(records, idx, entity_type=E, key_field="policy_number")
    assert out[0]["gross_premium"] == 0


def test_records_without_a_key_pass_through():
    out = ov.apply_overrides([{"gross_premium": 1}], {"x": make()},
                             entity_type=E, key_field="policy_number")
    assert out[0]["gross_premium"] == 1


def test_no_overrides_returns_the_input_untouched():
    records = [{"policy_number": "P1"}]
    assert ov.apply_overrides(records, {}, entity_type=E, key_field="policy_number") is records
