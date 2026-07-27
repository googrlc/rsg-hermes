"""Tests for the live AMS book read (hermes/ams/book.py).

Covers the flag gate, the PostgREST filter subset the call sites use, lineage
joining, caching, and the fallbacks. NowCerts + Supabase mocked.
"""

from __future__ import annotations

import threading

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
    # The second read is served from cache and refreshed behind the request, so
    # the refetch lands on the background thread rather than inline.
    book.await_refresh(timeout=5)
    assert nc.fetch_policies.call_count == 2


# ------------------------------------------------------------------ fallback

def test_ams_failure_falls_back_to_the_mirror(live):
    supa = _supa()
    supa.select.return_value = [{"policy_guid": "mirror"}]
    with patch.object(book, "fetch_book", side_effect=RuntimeError("AMS down")):
        rows = book.select_policies(supa, columns="policy_guid")
    assert rows == [{"policy_guid": "mirror"}]


# ------------------------------------------------- shared client & backoff
# Regression cover for the stall diagnosed 2026-07-27: NowCerts' password grant
# takes ~26s, and every book read used to build a fresh client (empty token
# cache) and re-pay it, which froze the API and timed out the MCP tools.

def test_uses_the_shared_client_when_none_is_passed(live):
    """No `nowcerts=` must reach get_client(), not a fresh NowCertsClient()."""
    nc = _nc()
    with patch("hermes.sync.nowcerts_client.get_client", return_value=nc) as shared:
        book.select_policies(_supa())
    shared.assert_called_once()
    nc.fetch_policies.assert_called_once()


def test_a_failed_read_backs_off_instead_of_retrying_immediately(live):
    """The AMS is hit once; the next call fails fast without a second timeout."""
    nc = _nc()
    nc.fetch_policies.side_effect = RuntimeError("read timed out")
    with pytest.raises(RuntimeError):
        book.fetch_book(_supa(), nowcerts=nc)
    with pytest.raises(book.AmsBookUnavailable):
        book.fetch_book(_supa(), nowcerts=nc)
    nc.fetch_policies.assert_called_once()


def test_backoff_degrades_to_the_mirror_rather_than_erroring(live):
    """select_policies() swallows the backoff, so callers still get rows."""
    nc = _nc()
    nc.fetch_policies.side_effect = RuntimeError("read timed out")
    supa = _supa()
    supa.select.return_value = [{"policy_guid": "mirror"}]
    with pytest.raises(RuntimeError):
        book.fetch_book(supa, nowcerts=nc)
    rows = book.select_policies(supa, columns="policy_guid", nowcerts=nc)
    assert rows == [{"policy_guid": "mirror"}]


def test_force_overrides_the_backoff(live):
    nc = _nc()
    nc.fetch_policies.side_effect = RuntimeError("read timed out")
    with pytest.raises(RuntimeError):
        book.fetch_book(_supa(), nowcerts=nc)
    nc.fetch_policies.side_effect = None
    book.fetch_book(_supa(), nowcerts=nc, force=True)
    assert nc.fetch_policies.call_count == 2


def test_a_good_read_retires_the_backoff(live, monkeypatch):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    nc.fetch_policies.side_effect = RuntimeError("read timed out")
    with pytest.raises(RuntimeError):
        book.fetch_book(_supa(), nowcerts=nc)
    nc.fetch_policies.side_effect = None
    book.fetch_book(_supa(), nowcerts=nc, force=True)
    # backoff cleared by the success -> a plain call goes through again
    book.fetch_book(_supa(), nowcerts=nc)
    assert nc.fetch_policies.call_count == 3


# ---------------------------------------------- stale-while-revalidate
# A full book pull is 5 pages against an API whose per-page latency swings
# between ~5s and ~30s. Making a request wait for one is what froze the API and
# 8s-timed-out the portal behind it, so only a cold cache may ever block.

def test_a_stale_cache_is_served_without_waiting_for_the_ams(live, monkeypatch):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    book.select_policies(_supa(), nowcerts=nc)      # cold: blocks, fills cache
    started = threading.Event()
    release = threading.Event()

    def _slow(*a, **k):
        started.set()
        release.wait(5)                              # a pull that never returns in time
        return []

    nc.fetch_policies.side_effect = _slow
    rows = book.select_policies(_supa(), nowcerts=nc)   # must NOT wait on _slow
    assert rows, "a stale book must still be served"
    assert started.wait(5), "the refresh should have been kicked off in the background"
    release.set()
    book.await_refresh(timeout=5)


def test_only_one_background_refresh_runs_at_a_time(live, monkeypatch):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    book.select_policies(_supa(), nowcerts=nc)
    release = threading.Event()
    nc.fetch_policies.side_effect = lambda *a, **k: (release.wait(5), [])[1]
    for _ in range(5):
        book.select_policies(_supa(), nowcerts=nc)
    release.set()
    book.await_refresh(timeout=5)
    # 1 cold pull + exactly 1 background refresh, not one per request
    assert nc.fetch_policies.call_count == 2


def test_a_cold_cache_still_blocks_and_raises(live):
    """With nothing cached there is nothing to serve, so the failure must surface
    and let select_policies() fall back to the mirror."""
    nc = _nc()
    nc.fetch_policies.side_effect = RuntimeError("read timed out")
    with pytest.raises(RuntimeError):
        book.fetch_book(_supa(), nowcerts=nc)


def test_a_failed_background_refresh_keeps_serving_the_stale_book(live, monkeypatch):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    book.select_policies(_supa(), nowcerts=nc)
    nc.fetch_policies.side_effect = RuntimeError("read timed out")
    rows = book.select_policies(_supa(), nowcerts=nc)
    book.await_refresh(timeout=5)
    assert rows, "a failed refresh must not take the cached book away"
    # and the failure is recorded, so the next stale read backs off instead of retrying
    assert book._cache["failed_at"] > 0
