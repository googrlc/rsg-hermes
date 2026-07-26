"""Tests for hermes.book_sync.health — NowCerts vs the canonical Supabase book."""

from __future__ import annotations

from hermes.book_sync.health import (
    build_carrier_breakdown,
    check_policy_count_agreement,
    find_carrier_name_mismatches,
    find_tombstoned_policies,
    normalize_carrier,
)


def _nc(number: str, *, carrier: str = "Progressive", status: str = "Active", premium: float = 1000.0):
    return {"number": number, "carrierName": carrier, "policyStatus": status, "premium": premium}


def _book(number: str, *, carrier: str = "Progressive", active: bool = True, premium: float = 1000.0):
    return {
        "policy_number": number, "carrier": carrier,
        "active": active, "premium_amount": premium,
    }


class TestPolicyCountAgreement:
    def test_matching_counts_are_ok(self):
        check = check_policy_count_agreement(
            nowcerts_policies=[_nc("A"), _nc("B")],
            canonical_policies=[_book("A"), _book("B")],
        )
        assert check.ok
        assert check.metrics["nowcerts_active"] == 2
        assert check.metrics["canonical_active"] == 2

    def test_cancelled_nowcerts_policies_are_excluded(self):
        check = check_policy_count_agreement(
            nowcerts_policies=[_nc("A"), _nc("B", status="Cancelled")],
            canonical_policies=[_book("A")],
        )
        assert check.ok
        assert check.metrics["nowcerts_active"] == 1

    def test_large_gap_flags_drift(self):
        check = check_policy_count_agreement(
            nowcerts_policies=[_nc(str(i)) for i in range(100)],
            canonical_policies=[_book("0")],
        )
        assert not check.ok
        assert check.metrics["delta"] == 99


class TestTombstonedPolicies:
    def test_clean_book_reports_nothing(self):
        affected, metrics = find_tombstoned_policies(
            nowcerts_policies=[_nc("A"), _nc("B")],
            canonical_policies=[_book("A"), _book("B")],
        )
        assert affected == []
        assert metrics["affected_count"] == 0

    def test_missing_row_is_reported(self):
        affected, metrics = find_tombstoned_policies(
            nowcerts_policies=[_nc("A"), _nc("B")],
            canonical_policies=[_book("A")],
        )
        assert affected == ["B"]
        assert metrics["missing_count"] == 1
        assert metrics["inactive_count"] == 0

    def test_row_marked_inactive_is_reported(self):
        """The two-writer failure: the row survives but gets flipped inactive."""
        affected, metrics = find_tombstoned_policies(
            nowcerts_policies=[_nc("A")],
            canonical_policies=[_book("A", active=False)],
        )
        assert affected == ["A"]
        assert metrics["inactive_count"] == 1

    def test_creates_and_tombstones_cancelling_out_still_surface(self):
        """A count check passes here; this check must not."""
        nc = [_nc("A"), _nc("B")]
        book = [_book("A", active=False), _book("C"), _book("D")]

        count_check = check_policy_count_agreement(
            nowcerts_policies=nc, canonical_policies=book,
        )
        assert count_check.metrics["nowcerts_active"] == count_check.metrics["canonical_active"]

        affected, metrics = find_tombstoned_policies(
            nowcerts_policies=nc, canonical_policies=book,
        )
        assert set(affected) == {"A", "B"}
        assert metrics["affected_count"] == 2

    def test_inactive_nowcerts_policies_are_not_expected_in_the_book(self):
        affected, metrics = find_tombstoned_policies(
            nowcerts_policies=[_nc("A", status="Cancelled")],
            canonical_policies=[],
        )
        assert affected == []
        assert metrics["affected_count"] == 0


class TestCarrierBreakdown:
    def test_premium_totals_per_carrier(self):
        rows = build_carrier_breakdown(
            nowcerts_policies=[_nc("A", premium=1000.0), _nc("B", carrier="Safeco", premium=500.0)],
            canonical_policies=[_book("A", premium=1000.0), _book("B", carrier="Safeco", premium=500.0)],
        )
        by_carrier = {r.carrier: r for r in rows}
        assert by_carrier["Progressive"].nowcerts_premium == 1000.0
        assert by_carrier["Progressive"].in_tolerance
        assert by_carrier["Safeco"].canonical_premium == 500.0

    def test_premium_gap_falls_out_of_tolerance(self):
        rows = build_carrier_breakdown(
            nowcerts_policies=[_nc("A", premium=1000.0)],
            canonical_policies=[_book("A", premium=100.0)],
        )
        assert not rows[0].in_tolerance
        assert rows[0].premium_delta == 900.0


class TestCarrierNameMismatches:
    def test_agreement_reports_no_mismatch(self):
        mismatches, metrics = find_carrier_name_mismatches(
            nowcerts_policies=[_nc("A", carrier="Progressive")],
            canonical_policies=[_book("A", carrier="Progressive")],
        )
        assert mismatches == []
        assert metrics["agree_count"] == 1

    def test_disagreement_is_surfaced_with_nowcerts_as_canonical(self):
        mismatches, metrics = find_carrier_name_mismatches(
            nowcerts_policies=[_nc("A", carrier="PROGRESSIVE MOUNTAIN INS CO")],
            canonical_policies=[_book("A", carrier="Progressive")],
        )
        assert metrics["mismatch_count"] == 1
        assert mismatches[0].nowcerts_carrier == "PROGRESSIVE MOUNTAIN INS CO"
        assert mismatches[0].canonical_carrier == "Progressive"

    def test_one_sided_policies_are_counted_not_mismatched(self):
        mismatches, metrics = find_carrier_name_mismatches(
            nowcerts_policies=[_nc("A")],
            canonical_policies=[_book("B")],
        )
        assert mismatches == []
        assert metrics["nc_only_count"] == 1
        assert metrics["book_only_count"] == 1


class TestNormalizeCarrier:
    def test_strips_legal_suffixes_and_prefixes(self):
        assert normalize_carrier("GEICO CHOICE INS CO") == "geico"
        assert normalize_carrier("x_Geico") == "geico"

    def test_keeps_distinct_brand_stems_apart(self):
        assert normalize_carrier("Geico Marine") == "geico marine"

    def test_blank_input_returns_empty(self):
        assert normalize_carrier(None) == ""
        assert normalize_carrier("") == ""
