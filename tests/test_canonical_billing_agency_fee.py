"""NowCerts billingType + agencyFee → canonical book."""
from __future__ import annotations

from hermes_core.canonical import (
    _POLICY_COLS,
    _map_policy_volatile,
    normalize_billing_type,
)


def test_billing_type_agency_bill():
    row = _map_policy_volatile({
        "status": "Active",
        "billingType": "agency bill",
        "agencyFee": 75.5,
        "effectiveDate": "2026-07-01",
        "expirationDate": "2027-07-01",
        "number": "P-AB",
        "totalPremium": 1200,
    })
    assert row["billing_type"] == "Agency Bill"
    assert row["agency_fee_amount"] == 75.5


def test_billing_type_direct_bill_100():
    assert normalize_billing_type("DB100") == "Direct Bill 100"
    assert normalize_billing_type("Agency Bill 100") == "Agency Bill 100"
    assert normalize_billing_type("") is None


def test_billing_type_nowcerts_underscore_forms():
    # PolicyDetailList returns these spellings live.
    assert normalize_billing_type("Direct_Bill_100") == "Direct Bill 100"
    assert normalize_billing_type("Agency_Bill") == "Agency Bill"
    assert normalize_billing_type("Direct_Bill_Autopay") == "Direct Bill"


def test_billing_fields_in_policy_cols():
    assert "billing_type" in _POLICY_COLS
    assert "agency_fee_amount" in _POLICY_COLS
