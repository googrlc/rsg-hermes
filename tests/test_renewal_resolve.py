"""Unit tests for hermes/renewals/resolve.py — the exact-identity resolver.

NowCerts is the source of truth; every read is mocked. Synthetic identifiers
only — no real client names or policy numbers.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from hermes.renewals import resolve

TODAY = date(2026, 7, 15)


def _detail(*, number="TST-0001", guid="pol-guid-1", insured="ins-guid-1",
            status="Active", eff="2026-01-01", exp="2027-01-01"):
    return {
        "databaseId": guid,
        "insuredDatabaseId": insured,
        "policyNumber": number,
        "carrierName": "Test Carrier",
        "lineOfBusiness": "Commercial Auto",
        "effectiveDate": eff,
        "expirationDate": exp,
        "premium": 5000,
        "policyStatus": status,
    }


def _nc(detail):
    nc = MagicMock()
    want = None if detail is None else (detail.get("policyNumber") if isinstance(detail, dict) else None)

    def _find(number):
        if isinstance(detail, dict) and detail.get("_ambiguous"):
            return detail
        return detail if (detail is not None and number == want) else None

    nc.find_policy_by_number.side_effect = _find
    nc.is_insured_active.return_value = True
    return nc


def _supa(rows):
    supa = MagicMock()
    supa.select.return_value = rows
    return supa


# ------------------------------------------------------------- normalize

def test_normalize_maps_nowcerts_fields():
    p = resolve.normalize_nowcerts_policy(_detail(number="ABC-1"), client_name="Acme LLC")
    assert p["policyNumber"] == "ABC-1"
    assert p["policy_guid"] == "pol-guid-1"
    assert p["insured_database_id"] == "ins-guid-1"
    assert p["accountName"] == "Acme LLC"
    assert p["carrier"] == "Test Carrier"
    assert p["expiration_date"] == "2027-01-01"
    assert p["current_premium"] == 5000
    assert p["source"] == "nowcerts"


# ------------------------------------------------------------- by policy number

def test_resolve_by_number_ok():
    r = resolve.resolve_exact_policy(
        _nc(_detail(number="TST-0001")),
        policy_number="TST-0001",
        supa=_supa([{"client_name": "Acme LLC", "policy_number": "TST-0001"}]),
        today=TODAY,
    )
    assert r.ok and r.reason == resolve.RESOLVED
    assert r.policy["policyNumber"] == "TST-0001"
    assert r.policy["accountName"] == "Acme LLC"       # hydrated from candidate
    assert r.eligibility is not None                    # live revalidation ran


def test_resolve_by_number_not_found():
    r = resolve.resolve_exact_policy(_nc(None), policy_number="NOPE-9", supa=_supa([]), today=TODAY)
    assert not r.ok and r.reason == resolve.NOT_FOUND


def test_resolve_by_number_no_fuzzy():
    # NowCerts returns nothing for the exact number → not a prefix/fuzzy hit.
    r = resolve.resolve_exact_policy(_nc(_detail(number="TST-00010")), policy_number="TST-0001",
                                     supa=_supa([]), today=TODAY)
    assert not r.ok and r.reason == resolve.NOT_FOUND


def test_resolve_ambiguous_number_blocks():
    ambiguous = {"_ambiguous": True, "matches": [_detail(guid="a"), _detail(guid="b")]}
    r = resolve.resolve_exact_policy(_nc(ambiguous), policy_number="DUP-1", supa=_supa([]), today=TODAY)
    assert not r.ok and r.reason == resolve.AMBIGUOUS
    assert len(r.matches) == 2


# ------------------------------------------------------------- by GUID

def test_resolve_by_guid_via_candidate():
    supa = _supa([{"policy_number": "TST-0001", "client_name": "Acme LLC",
                   "nowcerts_policy_guid": "pol-guid-1"}])
    r = resolve.resolve_exact_policy(_nc(_detail(number="TST-0001")), policy_guid="pol-guid-1",
                                     supa=supa, today=TODAY)
    assert r.ok and r.reason == resolve.RESOLVED
    assert r.policy["policyNumber"] == "TST-0001"


def test_resolve_by_guid_ambiguous_candidates():
    supa = _supa([{"policy_number": "A"}, {"policy_number": "B"}])
    r = resolve.resolve_exact_policy(_nc(None), policy_guid="dup-guid", supa=supa, today=TODAY)
    assert not r.ok and r.reason == resolve.AMBIGUOUS


def test_resolve_by_guid_needs_supa():
    r = resolve.resolve_exact_policy(_nc(None), policy_guid="g", supa=None, today=TODAY)
    assert not r.ok and r.reason == resolve.NEED_IDENTIFIER


# ------------------------------------------------------------- no identifier

def test_resolve_needs_identifier():
    r = resolve.resolve_exact_policy(_nc(None), today=TODAY)
    assert not r.ok and r.reason == resolve.NEED_IDENTIFIER


def test_resolve_enriches_thin_read_from_candidate():
    """A sparse find_policy_by_number read (no effective date/status) + a vetted
    candidate row must resolve ELIGIBLE, not get downgraded to needs_verification."""
    thin = {"databaseId": "g1", "insuredDatabaseId": "i1", "policyNumber": "TST-0001",
            "carrierName": "GEICO", "expirationDate": "2026-10-29"}  # no effective/status/premium
    cand = {"client_name": "Acme LLC", "policy_number": "TST-0001",
            "effective_date": "2025-10-29", "expiration_date": "2026-10-29",
            "premium_current": 12000, "line_of_business": "Commercial Auto",
            "normalized_status": "Active"}
    r = resolve.resolve_exact_policy(_nc(thin), policy_number="TST-0001", supa=_supa([cand]), today=TODAY)
    assert r.ok and r.reason == resolve.RESOLVED
    assert r.policy["effective_date"] == "2025-10-29"   # backfilled from candidate
    assert r.policy["status"] == "Active"               # backfilled -> eligibility can read it
    assert r.policy["current_premium"] == 12000
    assert r.eligibility.state == "eligible"
