"""NowCerts cancellationDate → canonical_policies.cancellation_date."""
from __future__ import annotations

from hermes_core.canonical import _POLICY_COLS, _map_policy_volatile


def test_cancellation_date_mapped_from_camel_case():
    row = _map_policy_volatile({
        "status": "Cancelled",
        "effectiveDate": "2026-01-01T00:00:00",
        "expirationDate": "2027-01-01T00:00:00",
        "cancellationDate": "2026-06-15T12:00:00",
        "carrierName": "Progressive",
        "number": "P-1",
        "databaseId": "guid-1",
        "insuredDatabaseId": "ins-1",
        "totalPremium": 1200,
    })
    assert row["cancellation_date"] == "2026-06-15"
    assert row["expiration_date"] == "2027-01-01"
    assert row["effective_date"] == "2026-01-01"


def test_cancellation_date_mapped_from_pascal_case():
    row = _map_policy_volatile({
        "Status": "Cancelled",
        "EffectiveDate": "2026-01-01",
        "ExpirationDate": "2027-01-01",
        "CancellationDate": "2026-03-01",
    })
    assert row["cancellation_date"] == "2026-03-01"


def test_cancellation_date_absent_is_none():
    row = _map_policy_volatile({
        "status": "Active",
        "effectiveDate": "2026-01-01",
        "expirationDate": "2027-01-01",
    })
    assert row["cancellation_date"] is None


def test_cancellation_date_in_policy_cols():
    assert "cancellation_date" in _POLICY_COLS
