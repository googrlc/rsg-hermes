"""Tests for the commission ingest job (crm_commissions -> commission_ledger)."""

import pytest
from unittest import mock


def test_normalize_carrier():
    from hermes.jobs.commission_ingest import _normalize_carrier
    assert _normalize_carrier("Progressive Mountain Ins Co") == "PROGRESSIVE MOUNTAIN INS CO"
    assert _normalize_carrier("  PROGRESSIVE  ") == "PROGRESSIVE"
    assert _normalize_carrier(None) == ""
    assert _normalize_carrier("") == ""


def test_normalize_lob():
    from hermes.jobs.commission_ingest import _normalize_lob
    assert _normalize_lob("Workers Comp") == "Workers Comp"
    assert _normalize_lob("workers comp") == "Workers Comp"
    assert _normalize_lob("WC") == "Workers Comp"
    assert _normalize_lob("GL") == "General Liability"


def test_build_rule_index():
    from hermes.jobs.commission_ingest import _build_rule_index
    rules = [
        {"carrier_name": "BIBERK", "lob": "Workers Comp", "active": True, "nb_percent": 8.0},
        {"carrier_name": "PROGRESSIVE", "lob": "Personal Auto", "active": True, "nb_percent": 10.0},
        {"carrier_name": "Various", "lob": "ALL", "active": True, "nb_percent": None},
        {"carrier_name": "OLD CARRIER", "lob": "BOP", "active": False, "nb_percent": 5.0},
    ]
    idx = _build_rule_index(rules)
    assert ("BIBERK", "Workers Comp") in idx
    assert ("PROGRESSIVE", "Personal Auto") in idx
    assert ("VARIOUS", "ALL") not in idx
    assert ("OLD CARRIER", "BOP") not in idx


def test_match_rule_exact():
    from hermes.jobs.commission_ingest import _match_rule, _build_rule_index
    rules = [
        {"carrier_name": "BIBERK", "lob": "Workers Comp", "active": True, "nb_percent": 8.0, "id": "r1"},
    ]
    idx = _build_rule_index(rules)
    result = _match_rule("BIBERK", "Workers Comp", idx, rules)
    assert result is not None
    assert result["id"] == "r1"


def test_match_rule_prefix():
    from hermes.jobs.commission_ingest import _match_rule, _build_rule_index
    rules = [
        {"carrier_name": "PROGRESSIVE MOUNTAIN", "lob": "Personal Auto", "active": True, "nb_percent": 10.0, "id": "r1"},
    ]
    idx = _build_rule_index(rules)
    # "PROGRESSIVE MOUNTAIN INS CO" should match "PROGRESSIVE MOUNTAIN"
    result = _match_rule("Progressive Mountain Ins Co", "Personal Auto", idx, rules)
    assert result is not None
    assert result["id"] == "r1"


def test_match_rule_no_match():
    from hermes.jobs.commission_ingest import _match_rule, _build_rule_index
    rules = [
        {"carrier_name": "BIBERK", "lob": "Workers Comp", "active": True, "nb_percent": 8.0},
    ]
    idx = _build_rule_index(rules)
    result = _match_rule("UNKNOWN CARRIER", "Unknown LOB", idx, rules)
    assert result is None


def test_compute_expected_commission_new():
    from hermes.jobs.commission_ingest import _compute_expected_commission
    rule = {"nb_percent": 10.0, "renewal_percent": 8.0}
    assert _compute_expected_commission(5000.0, rule, is_renewal=False) == 500.0


def test_compute_expected_commission_renewal():
    from hermes.jobs.commission_ingest import _compute_expected_commission
    rule = {"nb_percent": 10.0, "renewal_percent": 8.0}
    assert _compute_expected_commission(5000.0, rule, is_renewal=True) == 400.0


def test_compute_expected_commission_no_rate():
    from hermes.jobs.commission_ingest import _compute_expected_commission
    rule = {"nb_percent": None, "renewal_percent": None}
    assert _compute_expected_commission(5000.0, rule, is_renewal=False) is None


def test_compute_expected_commission_no_premium():
    from hermes.jobs.commission_ingest import _compute_expected_commission
    rule = {"nb_percent": 10.0, "renewal_percent": 8.0}
    assert _compute_expected_commission(0.0, rule, is_renewal=False) is None
    assert _compute_expected_commission(-100.0, rule, is_renewal=False) is None


def test_is_renewal():
    from hermes.jobs.commission_ingest import _is_renewal
    assert _is_renewal({"policy_status": "Renewed"}) is True
    assert _is_renewal({"policy_status": "Up for Renewal"}) is True
    assert _is_renewal({"policy_status": "Renewing"}) is True
    assert _is_renewal({"policy_status": "Active"}) is False
    assert _is_renewal({"policy_status": ""}) is False
    assert _is_renewal({}) is False


def test_ingest_result_ok():
    from hermes.jobs.commission_ingest import IngestResult
    r = IngestResult(total=10, inserted=5, updated=3, skipped_no_rule=2)
    assert r.ok is True
    assert "total=10" in r.message

    r2 = IngestResult(failed=1)
    assert r2.ok is False


def test_commissionable_statuses():
    from hermes.jobs.commission_ingest import COMMISSIONABLE_STATUSES
    assert "Active" in COMMISSIONABLE_STATUSES
    assert "Renewed" in COMMISSIONABLE_STATUSES
    assert "Expired" not in COMMISSIONABLE_STATUSES
    assert "Cancelled" not in COMMISSIONABLE_STATUSES
    assert "Flat Cancel" not in COMMISSIONABLE_STATUSES


def test_is_chargeback():
    from hermes.jobs.commission_ingest import _is_chargeback
    # Cancelled after 7/1 with expiration date
    assert _is_chargeback({"policy_status": "Cancelled", "expiration_date": "2026-07-15"}) is True
    assert _is_chargeback({"policy_status": "Expired", "expiration_date": "2026-08-01"}) is True
    assert _is_chargeback({"policy_status": "Flat Cancel", "expiration_date": "2026-07-01"}) is True
    # Cancelled before 7/1 — not included
    assert _is_chargeback({"policy_status": "Cancelled", "expiration_date": "2026-06-30"}) is False
    # Active — not a chargeback
    assert _is_chargeback({"policy_status": "Active", "expiration_date": "2026-07-15"}) is False
    # No date — not included
    assert _is_chargeback({"policy_status": "Cancelled", "expiration_date": ""}) is False
    assert _is_chargeback({"policy_status": "Cancelled"}) is False
    # Falls back to effective_date if no expiration
    assert _is_chargeback({"policy_status": "Expired", "effective_date": "2026-07-10"}) is True
