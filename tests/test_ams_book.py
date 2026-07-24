"""Tests for the live AMS book read (hermes/ams/book.py).

Covers the flag gate, the PostgREST filter subset the call sites use, lineage
joining, caching, and the fallbacks. NowCerts + Supabase mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.ams import book


@pytest.fixture(autouse=True)
def _clear_cache():
    book.invalidate_cache()
    yield
    book.invalidate_cache()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("HERMES_AMS_LIVE_READS", "1")


def _nc(records=None):
    c = MagicMock()
    c.fetch_policies.return_value = records if records is not None else [
        {
            "databaseId": "g1", "number": "P-1", "insuredDatabaseId": "i1",
            "carrierName": "CNA", "status": "Active", "active": True,
            "totalPremium": 640.0, "effectiveDate": "2026-07-17T00:00:00-05:00",
            "expirationDate": "2027-07-17T00:00:00-05:00",
            "lineOfBusinesses": [{"lineOfBusinessName": "Worker's Compensation"}],
        },
        {
            "databaseId": "g2", "number": "P-2", "insuredDatabaseId": "i2",
            "carrierName": "Progressive", "status": "Expired", "active": False,
            "totalPremium": 2047.0, "effectiveDate": "2025-11-20T00:00:00-06:00",
            "expirationDate": "2026-05-20T00:00:00-05:00",
            "lineOfBusinesses": [{"lineOfBusinessName": "Personal Auto"}],
        },
    ]
    return c


def _supa(lineage=None):
    s = MagicMock()
    s.select.return_value = lineage if lineage is not None else []
    return s


# ------------------------------------------------------------------ flag gate

def test_delegates_to_supabase_when_flag_unset(monkeypatch):
    monkeypatch.delenv("HERMES_AMS_LIVE_READS", raising=False)
    supa = _supa()
    supa.select.return_value = [{"policy_guid": "mirror"}]
    out = book.select_policies(supa, columns="policy_guid", limit=5)
    assert out == [{"policy_guid": "mirror"}]
    supa.select.assert_called_once_with(
        "canonical_policies", columns="policy_guid", params={}, limit=5
    )


def test_reads_the_ams_when_flag_set(live):
    nc = _nc()
    rows = book.select_policies(_supa(), nowcerts=nc, limit=10)
    nc.fetch_policies.assert_called_once()
    assert {r["policy_guid"] for r in rows} == {"g1", "g2"}


# ------------------------------------------------------------------ mapping

def test_maps_lob_premium_and_active_from_raw_nowcerts(live):
    rows = book.select_policies(_supa(), nowcerts=_nc(), limit=10)
    g1 = next(r for r in rows if r["policy_guid"] == "g1")
    # lineOfBusinesses is a LIST of objects; premium lives in totalPremium.
    assert g1["lines_of_business"] == "Worker's Compensation"
    assert g1["premium_amount"] == 640.0
    assert g1["carrier"] == "CNA"
    assert g1["active"] is True
    assert g1["expiration_date"] == "2027-07-17"


def test_zero_agency_commission_stays_zero(live):
    """0.0 is a real commission. Picking by truthiness would collapse it to None
    and send commission_sync to its rule-based fallback instead."""
    nc = _nc([{"databaseId": "g1", "number": "P-1", "totalAgencyCommission": 0.0}])
    rows = book.select_policies(_supa(), nowcerts=nc)
    assert rows[0]["agency_commission_amount"] == 0.0


def test_agency_commission_absent_is_none(live):
    nc = _nc([{"databaseId": "g1", "number": "P-1"}])
    rows = book.select_policies(_supa(), nowcerts=nc)
    assert rows[0]["agency_commission_amount"] is None


def test_records_without_a_guid_are_dropped(live):
    nc = _nc([{"number": "no-guid", "status": "Active"}])
    assert book.select_policies(_supa(), nowcerts=nc) == []


# ------------------------------------------------------------------ lineage

def test_lineage_is_joined_from_supabase(live):
    supa = _supa([{"policy_guid": "g1", "renewed_policy": "OLD-1"}])
    rows = book.select_policies(supa, nowcerts=_nc(), limit=10)
    by_guid = {r["policy_guid"]: r for r in rows}
    # renewed_policy is NOT in the AMS — it must come from policy_lineage.
    assert by_guid["g1"]["renewed_policy"] == "OLD-1"
    assert by_guid["g2"]["renewed_policy"] is None


def test_lineage_failure_does_not_break_the_book(live):
    supa = MagicMock()
    supa.select.side_effect = RuntimeError("lineage table gone")
    rows = book.select_policies(supa, nowcerts=_nc(), limit=10)
    assert len(rows) == 2 and all(r["renewed_policy"] is None for r in rows)


# ------------------------------------------------------------------ filtering

def test_eq_filter(live):
    rows = book.select_policies(
        _supa(), nowcerts=_nc(), params={"nowcerts_insured_guid": "eq.i2"}
    )
    assert [r["policy_guid"] for r in rows] == ["g2"]


def test_in_filter(live):
    rows = book.select_policies(
        _supa(), nowcerts=_nc(), params={"nowcerts_insured_guid": "in.(i1,i2)"}
    )
    assert len(rows) == 2


def test_order_desc_and_limit(live):
    rows = book.select_policies(
        _supa(), nowcerts=_nc(), params={"order": "expiration_date.desc"}, limit=1
    )
    assert [r["policy_guid"] for r in rows] == ["g1"]


def test_unsupported_filter_raises_rather_than_widening(live):
    # Silently ignoring a filter would return the whole book to a caller that
    # asked for one client's policies.
    with pytest.raises(ValueError, match="unsupported filter"):
        book.select_policies(_supa(), nowcerts=_nc(), params={"premium_amount": "gt.100"})


def test_column_projection(live):
    rows = book.select_policies(_supa(), nowcerts=_nc(), columns="policy_number,carrier")
    assert set(rows[0]) == {"policy_number", "carrier"}


# ------------------------------------------------------------------ caching

def test_book_is_cached_across_calls(live):
    nc = _nc()
    supa = _supa()
    book.select_policies(supa, nowcerts=nc)
    book.select_policies(supa, nowcerts=nc)
    nc.fetch_policies.assert_called_once()


def test_invalidate_cache_forces_a_refetch(live):
    nc = _nc()
    book.select_policies(_supa(), nowcerts=nc)
    book.invalidate_cache()
    book.select_policies(_supa(), nowcerts=nc)
    assert nc.fetch_policies.call_count == 2


def test_expired_ttl_refetches(live, monkeypatch):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    book.select_policies(_supa(), nowcerts=nc)
    book.select_policies(_supa(), nowcerts=nc)
    assert nc.fetch_policies.call_count == 2


# ------------------------------------------------------------------ fallback

def test_ams_failure_falls_back_to_the_mirror(live):
    supa = _supa()
    supa.select.return_value = [{"policy_guid": "mirror"}]
    with patch.object(book, "fetch_book", side_effect=RuntimeError("AMS down")):
        rows = book.select_policies(supa, columns="policy_guid")
    assert rows == [{"policy_guid": "mirror"}]
