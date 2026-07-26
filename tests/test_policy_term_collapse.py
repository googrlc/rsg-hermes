"""Collapsing renewal-overlap pairs to one current term per coverage.

A renewal is a lineage: the expiring term and its successor briefly coexist — two
"active" policies until the old one drops off — and the same policy can also
appear twice from dual-source imports. Against production, 163 active rows
collapse to 126: **37 rows, ~23% of the active book, are shown twice today.**

The dangerous failure here is over-merging. Folding two genuinely distinct
policies into one hides real coverage and understates premium, which is worse than
showing a duplicate. Hence grouping on the lineage root rather than on anything
fuzzy, and hence most of these tests being about what must NOT merge.
"""

from __future__ import annotations

from datetime import date

import pytest

from hermes.api import _collapse_to_current_terms, _term_sort_key


def pol(number, *, guid=None, insured="INS-1", lob="Commercial Auto",
        renewed_policy=None, eff="2026-01-01", exp="2027-01-01", premium=1000):
    return {
        "policy_number": number,
        "policy_guid": guid or f"g-{number}",
        "nowcerts_insured_guid": insured,
        "lines_of_business": lob,
        "renewed_policy": renewed_policy,
        "effective_date": eff,
        "expiration_date": exp,
        "premium_amount": premium,
    }


# --- collapsing -------------------------------------------------------------

def test_a_renewal_pair_collapses_to_the_newer_term():
    old = pol("P1", eff="2025-01-01", exp="2026-01-01")
    new = pol("P2", renewed_policy="P1", eff="2026-01-01", exp="2027-01-01")
    current, folded = _collapse_to_current_terms([old, new])
    assert folded == 1
    assert [p["policy_number"] for p in current] == ["P2"]
    assert current[0]["prior_terms"] == 1


def test_the_survivor_is_chosen_by_effective_date_not_input_order():
    new = pol("P2", renewed_policy="P1", eff="2026-01-01")
    old = pol("P1", eff="2025-01-01")
    for ordering in ([old, new], [new, old]):
        current, _ = _collapse_to_current_terms(ordering)
        assert current[0]["policy_number"] == "P2"


def test_a_duplicate_import_of_the_same_policy_collapses():
    """Same policy number twice from two import sources — no lineage link needed."""
    a = pol("P1", guid="g-a", eff="2026-01-01")
    b = pol("P1", guid="g-b", eff="2026-01-01")
    current, folded = _collapse_to_current_terms([a, b])
    assert len(current) == 1 and folded == 1


def test_a_three_term_lineage_keeps_only_the_latest():
    t1 = pol("P1", eff="2024-01-01")
    t2 = pol("P2", renewed_policy="P1", eff="2025-01-01")
    t3 = pol("P3", renewed_policy="P1", eff="2026-01-01")
    current, folded = _collapse_to_current_terms([t1, t2, t3])
    assert [p["policy_number"] for p in current] == ["P3"]
    assert folded == 2 and current[0]["prior_terms"] == 2


def test_prior_terms_is_zero_on_an_unrenewed_policy():
    current, folded = _collapse_to_current_terms([pol("P1")])
    assert folded == 0 and current[0]["prior_terms"] == 0


# --- what must NOT merge ----------------------------------------------------

def test_two_distinct_policies_never_merge():
    """The expensive mistake: folding real coverage away and understating premium."""
    current, folded = _collapse_to_current_terms([pol("P1"), pol("P2")])
    assert folded == 0
    assert {p["policy_number"] for p in current} == {"P1", "P2"}


def test_same_insured_different_lob_stays_separate():
    current, folded = _collapse_to_current_terms([
        pol("P1", lob="Commercial Auto"),
        pol("P2", lob="General Liability"),
    ])
    assert folded == 0 and len(current) == 2


def test_same_policy_number_different_insured_stays_separate():
    """Carriers reuse numbers across insureds; merging would cross clients."""
    current, folded = _collapse_to_current_terms([
        pol("P1", insured="INS-1"),
        pol("P1", insured="INS-2"),
    ])
    assert folded == 0 and len(current) == 2


def test_lob_grouping_is_case_and_whitespace_insensitive():
    current, folded = _collapse_to_current_terms([
        pol("P1", lob="Commercial Auto", eff="2025-01-01"),
        pol("P2", lob="  commercial auto  ", renewed_policy="P1", eff="2026-01-01"),
    ])
    assert folded == 1 and current[0]["policy_number"] == "P2"


# --- ordering and bad data --------------------------------------------------

def test_output_is_soonest_expiring_first():
    current, _ = _collapse_to_current_terms([
        pol("LATER", exp="2027-06-01"),
        pol("SOONER", exp="2026-09-01"),
    ])
    assert [p["policy_number"] for p in current] == ["SOONER", "LATER"]


def test_a_missing_expiration_sorts_last_not_first():
    """A null must not masquerade as the most urgent renewal."""
    current, _ = _collapse_to_current_terms([
        pol("NODATE", exp=None),
        pol("REAL", exp="2026-09-01"),
    ])
    assert current[0]["policy_number"] == "REAL"


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "2026-13-45"])
def test_an_unparseable_effective_date_sorts_oldest(bad):
    """It must lose to a real date rather than crash or win."""
    good = pol("GOOD", eff="2026-01-01")
    broken = pol("BROKEN", renewed_policy="GOOD", eff=bad)
    current, folded = _collapse_to_current_terms([good, broken])
    assert folded == 1
    assert current[0]["policy_number"] == "GOOD"


def test_term_sort_key_never_raises_on_junk():
    assert _term_sort_key({"effective_date": object(), "expiration_date": None}) is not None


def test_an_empty_book_is_handled():
    assert _collapse_to_current_terms([]) == ([], 0)


def test_a_blank_renewed_policy_falls_back_to_the_policy_number():
    """Empty string must not become a shared grouping root that merges everything."""
    current, folded = _collapse_to_current_terms([
        pol("P1", renewed_policy=""),
        pol("P2", renewed_policy="   "),
    ])
    assert folded == 0 and len(current) == 2


def test_folded_count_matches_the_rows_removed():
    """The invariant the cockpit reports: in == out + folded."""
    rows = [
        pol("A1", eff="2025-01-01"), pol("A2", renewed_policy="A1", eff="2026-01-01"),
        pol("B1", lob="General Liability"),
        pol("C1", insured="INS-9"), pol("C1", insured="INS-9", guid="g-dup"),
    ]
    current, folded = _collapse_to_current_terms(rows)
    assert len(current) + folded == len(rows)
