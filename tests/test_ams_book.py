"""Tests for the live AMS book read (hermes/ams/book.py).

Covers the flag gate, the PostgREST filter subset the call sites use, lineage
joining, caching, and the fallbacks. NowCerts + Supabase mocked.
"""

from __future__ import annotations

import threading
import time

from unittest.mock import MagicMock, patch

import pytest

from hermes.ams import book


@pytest.fixture(autouse=True)
def _clear_cache():
    # Refreshes run on a background thread, so a thread still in flight from the
    # previous test will happily write into _cache *after* invalidation and leak
    # state into this one. Drain first, then clear.
    book.await_refresh(timeout=5)
    book.invalidate_cache()
    yield
    book.await_refresh(timeout=5)
    book.invalidate_cache()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("HERMES_AMS_LIVE_READS", "1")



@pytest.fixture
def sync_refresh(monkeypatch):
    """Run refreshes inline. The decision to refresh is the logic under test;
    real threads here just make the suite flaky."""
    def _inline(supa, nowcerts):
        try:
            book._pull_book(supa, nowcerts)
        except Exception:
            pass
        finally:
            with book._lock:
                book._cache["refreshing"] = False
    monkeypatch.setattr(book, "_spawn_refresh", _inline)
    return _inline


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


def _prime(supa, nc):
    """Fill the cache synchronously.

    fetch_book() no longer blocks a caller on the AMS: a cold cache raises and
    refreshes in the background, so select_policies() falls back to the mirror.
    Tests that are exercising the mapper/filters (not the concurrency) want the
    book actually present, which is what force=True is for.
    """
    book.fetch_book(supa, nowcerts=nc, force=True)
    return supa


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
    _prime(_supa(), nc)
    rows = book.select_policies(_supa(), nowcerts=nc, limit=10)
    nc.fetch_policies.assert_called_once()
    assert {r["policy_guid"] for r in rows} == {"g1", "g2"}


# ------------------------------------------------------------------ mapping

def test_maps_lob_premium_and_active_from_raw_nowcerts(live):
    _prime(_supa(), _nc())
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
    _prime(_supa(), nc)
    rows = book.select_policies(_supa(), nowcerts=nc)
    assert rows[0]["agency_commission_amount"] == 0.0


def test_agency_commission_absent_is_none(live):
    nc = _nc([{"databaseId": "g1", "number": "P-1"}])
    _prime(_supa(), nc)
    rows = book.select_policies(_supa(), nowcerts=nc)
    assert rows[0]["agency_commission_amount"] is None


def test_records_without_a_guid_are_dropped(live):
    nc = _nc([{"number": "no-guid", "status": "Active"}])
    _prime(_supa(), nc)
    assert book.select_policies(_supa(), nowcerts=nc) == []


# ------------------------------------------------------------------ lineage

def test_lineage_is_joined_from_supabase(live):
    supa = _supa([{"policy_guid": "g1", "renewed_policy": "OLD-1"}])
    _prime(supa, _nc())
    rows = book.select_policies(supa, nowcerts=_nc(), limit=10)
    by_guid = {r["policy_guid"]: r for r in rows}
    # renewed_policy is NOT in the AMS — it must come from policy_lineage.
    assert by_guid["g1"]["renewed_policy"] == "OLD-1"
    assert by_guid["g2"]["renewed_policy"] is None


def test_lineage_failure_does_not_break_the_book(live):
    supa = MagicMock()
    supa.select.side_effect = RuntimeError("lineage table gone")
    _prime(supa, _nc())
    rows = book.select_policies(supa, nowcerts=_nc(), limit=10)
    assert len(rows) == 2 and all(r["renewed_policy"] is None for r in rows)


# ------------------------------------------------------------------ filtering

def test_eq_filter(live):
    _prime(_supa(), _nc())
    rows = book.select_policies(
        _supa(), nowcerts=_nc(), params={"nowcerts_insured_guid": "eq.i2"}
    )
    assert [r["policy_guid"] for r in rows] == ["g2"]


def test_in_filter(live):
    _prime(_supa(), _nc())
    rows = book.select_policies(
        _supa(), nowcerts=_nc(), params={"nowcerts_insured_guid": "in.(i1,i2)"}
    )
    assert len(rows) == 2


def test_order_desc_and_limit(live):
    _prime(_supa(), _nc())
    rows = book.select_policies(
        _supa(), nowcerts=_nc(), params={"order": "expiration_date.desc"}, limit=1
    )
    assert [r["policy_guid"] for r in rows] == ["g1"]


def test_unsupported_filter_raises_rather_than_widening(live):
    # Silently ignoring a filter would return the whole book to a caller that
    # asked for one client's policies.
    _prime(_supa(), _nc())
    with pytest.raises(ValueError, match="unsupported filter"):
        book.select_policies(_supa(), nowcerts=_nc(), params={"premium_amount": "gt.100"})


def test_column_projection(live):
    _prime(_supa(), _nc())
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
    """Refreshes are backgrounded now, so drive them with force to stay
    deterministic — the point is that invalidation re-pulls, not when."""
    nc = _nc()
    _prime(_supa(), nc)
    book.invalidate_cache()
    _prime(_supa(), nc)
    assert nc.fetch_policies.call_count == 2


def test_expired_ttl_refetches(live, monkeypatch, sync_refresh):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    _prime(_supa(), nc)                            # warm the cache: 1 pull
    book.select_policies(_supa(), nowcerts=nc)     # stale -> serves cache, refreshes behind
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

def test_uses_the_shared_client_when_none_is_passed(live, sync_refresh):
    """No `nowcerts=` must reach get_client(), not a fresh NowCertsClient()."""
    nc = _nc()
    with patch("hermes_integrations.nowcerts_client.get_client", return_value=nc) as shared:
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
        book.fetch_book(_supa(), nowcerts=nc, force=True)
    assert book._cache["failed_at"] > 0
    nc.fetch_policies.side_effect = None
    book.fetch_book(_supa(), nowcerts=nc, force=True)
    assert book._cache["failed_at"] == 0, "a good read must retire the backoff"
    assert nc.fetch_policies.call_count == 2


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
    """Single-flight only means anything with real concurrency, so this one keeps
    real threads while its siblings run the refresh inline."""
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    _prime(_supa(), nc)                       # 1 pull, cache warm
    entered, release = threading.Event(), threading.Event()

    def _blocking(*a, **k):
        entered.set()
        release.wait(5)
        return []

    nc.fetch_policies.side_effect = _blocking
    book.select_policies(_supa(), nowcerts=nc)        # spawns the refresh
    assert entered.wait(5), "the refresh should have started"
    for _ in range(4):                                # all land while it is in flight
        book.select_policies(_supa(), nowcerts=nc)
    release.set()
    book.await_refresh(timeout=5)
    assert nc.fetch_policies.call_count == 2, "one refresh, not one per request"


def test_a_cold_cache_still_blocks_and_raises(live):
    """With nothing cached there is nothing to serve, so the failure must surface
    and let select_policies() fall back to the mirror."""
    nc = _nc()
    nc.fetch_policies.side_effect = RuntimeError("read timed out")
    with pytest.raises(RuntimeError):
        book.fetch_book(_supa(), nowcerts=nc)


def test_a_failed_background_refresh_keeps_serving_the_stale_book(live, monkeypatch, sync_refresh):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    book.select_policies(_supa(), nowcerts=nc)
    nc.fetch_policies.side_effect = RuntimeError("read timed out")
    rows = book.select_policies(_supa(), nowcerts=nc)
    assert rows, "a failed refresh must not take the cached book away"
    # and the failure is recorded, so the next stale read backs off instead of retrying
    assert book._cache["failed_at"] > 0


def test_a_cold_cache_never_blocks_the_caller(live):
    """The regression that mattered: with the AMS failing rather than merely slow,
    the pull never succeeds, so the cache never fills, so 'block just this once'
    was every request. Measured 77s on /api/clients before this."""
    nc = _nc()
    started, release = threading.Event(), threading.Event()

    def _never_returns(*a, **k):
        started.set()
        release.wait(10)
        return []

    nc.fetch_policies.side_effect = _never_returns
    t0 = time.time()
    with pytest.raises(book.AmsBookUnavailable):
        book.fetch_book(_supa(), nowcerts=nc)
    assert time.time() - t0 < 2, "a cold read must not wait on the AMS"
    assert started.wait(5), "it should still have started the pull in the background"
    release.set()
    book.await_refresh(timeout=10)


def test_a_cold_cache_degrades_to_the_mirror_rather_than_hanging(live):
    """select_policies() swallows AmsBookUnavailable, so the CRM stays up on
    mirror data while the AMS pull runs behind it."""
    nc = _nc()
    release = threading.Event()
    nc.fetch_policies.side_effect = lambda *a, **k: (release.wait(10), [])[1]
    supa = _supa()
    supa.select.return_value = [{"policy_guid": "mirror"}]
    t0 = time.time()
    rows = book.select_policies(supa, columns="policy_guid", nowcerts=nc)
    assert rows == [{"policy_guid": "mirror"}]
    assert time.time() - t0 < 2
    release.set()
    book.await_refresh(timeout=10)


# ------------------------------------------------- incremental refresh
# A full pull is 5 sequential pages against an API measured at ~40% success per
# page. A changeDate delta is normally one page, so it lands far more often and
# finishes in seconds — which matters when the AMS is this flaky.

def test_first_pull_is_full_then_refreshes_are_incremental(live, monkeypatch):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    book.fetch_book(_supa(), nowcerts=nc, force=True)
    assert nc.fetch_policies.call_args.kwargs.get("since") is None, "first pull must be full"
    book.fetch_book(_supa(), nowcerts=nc, force=True)
    assert nc.fetch_policies.call_args.kwargs.get("since"), "second pull must be a delta"


def test_a_delta_merges_into_the_book_rather_than_replacing_it(live, monkeypatch):
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    nc = _nc()
    book.fetch_book(_supa(), nowcerts=nc, force=True)          # g1 + g2
    # Delta reports only g1, changed. g2 must survive.
    nc.fetch_policies.return_value = [
        {"databaseId": "g1", "number": "P-1-UPDATED", "insuredDatabaseId": "i1",
         "carrierName": "CNA", "status": "Active", "active": True},
    ]
    rows = book.fetch_book(_supa(), nowcerts=nc, force=True)
    by = {r["policy_guid"]: r for r in rows}
    assert set(by) == {"g1", "g2"}, "a delta must not drop untouched policies"
    assert by["g1"]["policy_number"] == "P-1-UPDATED"


def test_a_full_pull_is_forced_periodically_to_catch_deletions(live, monkeypatch):
    """A changeDate delta never reports a removal, so a deleted policy would sit
    in the cache forever without this."""
    monkeypatch.setenv("HERMES_AMS_BOOK_TTL", "0")
    monkeypatch.setenv("HERMES_AMS_FULL_REFRESH", "0")   # every refresh is due a full pull
    nc = _nc()
    book.fetch_book(_supa(), nowcerts=nc, force=True)
    book.fetch_book(_supa(), nowcerts=nc, force=True)
    assert nc.fetch_policies.call_args.kwargs.get("since") is None


# ------------------------------------------------------------- clients
# Policies went live and clients did not — nobody noticed, because a stale client
# still renders. It surfaced when duplicates deleted in NowCerts kept appearing
# in the CRM: /api/clients was reading a mirror whose sync has been off since
# 2026-07-24, carrying 54 clients that no longer exist in the AMS.

@pytest.fixture(autouse=True)
def _clear_client_cache():
    book.await_client_refresh(timeout=5)
    book.invalidate_client_cache()
    yield
    book.await_client_refresh(timeout=5)
    book.invalidate_client_cache()


def _nci(records=None):
    c = MagicMock()
    c.fetch_insureds.return_value = records if records is not None else [
        {"databaseId": "i1", "commercialName": "Acme Holdings LLC", "city": "Atlanta",
         "state": "Georgia", "eMail": "ops@acme.example", "active": True},
        {"databaseId": "i2", "firstName": "Jane", "lastName": "Doe", "city": "Marietta",
         "state": "Georgia", "active": True},
    ]
    return c


def test_clients_come_from_the_ams_when_live_reads_are_on(live):
    nc = _nci()
    book.fetch_clients(_supa(), nowcerts=nc, force=True)
    rows = book.select_clients(_supa(), nowcerts=nc)
    assert {r["insured_name"] for r in rows} == {"Acme Holdings LLC", "Jane Doe"}


def test_a_commercial_name_makes_it_commercial_and_a_person_personal(live):
    nc = _nci()
    book.fetch_clients(_supa(), nowcerts=nc, force=True)
    by = {r["insured_name"]: r for r in book.select_clients(_supa(), nowcerts=nc)}
    assert by["Acme Holdings LLC"]["client_type"] == "Commercial"
    assert by["Jane Doe"]["client_type"] == "Personal"


def test_an_insured_without_a_guid_is_dropped(live):
    """No stable key means it cannot be addressed, linked, or corrected."""
    nc = _nci([{"commercialName": "No Id Co"}])
    assert book.fetch_clients(_supa(), nowcerts=nc, force=True) == []


def test_clients_fall_back_to_the_mirror_when_the_ams_fails(live):
    nc = _nci()
    nc.fetch_insureds.side_effect = RuntimeError("read timed out")
    supa = _supa()
    supa.select.return_value = [{"insured_name": "from mirror"}]
    rows = book.select_clients(supa, columns="insured_name", nowcerts=nc)
    assert rows == [{"insured_name": "from mirror"}]


def test_the_mirror_is_used_when_live_reads_are_off(monkeypatch):
    monkeypatch.delenv("HERMES_AMS_LIVE_READS", raising=False)
    supa = _supa()
    supa.select.return_value = [{"insured_name": "mirror"}]
    assert book.select_clients(supa, columns="insured_name") == [{"insured_name": "mirror"}]
    supa.select.assert_called_once_with(
        "canonical_clients", columns="insured_name", params={}, limit=None
    )


def test_a_cold_client_cache_never_blocks_the_caller(live):
    nc = _nci()
    started, release = threading.Event(), threading.Event()

    def _never(*a, **k):
        started.set(); release.wait(10); return []

    nc.fetch_insureds.side_effect = _never
    t0 = time.time()
    with pytest.raises(book.AmsBookUnavailable):
        book.fetch_clients(_supa(), nowcerts=nc)
    assert time.time() - t0 < 2
    assert started.wait(5)
    release.set()
    book.await_client_refresh(timeout=10)
# ------------------------------------------------------------- quotes
# In NowCerts a quote is a Policy row with isQuote=true. The book pulled
# PolicyDetailList wholesale and never checked the flag, so quotes were counted
# as bound policies — while quote_sync.py was independently syncing those same
# rows into `opportunities`. One record, counted twice.

def test_quotes_are_not_policies(live):
    nc = _nc([
        {"databaseId": "g1", "number": "P-1", "status": "Active", "isQuote": False},
        {"databaseId": "q1", "number": "Q-1", "status": "Quoted", "isQuote": True},
    ])
    _prime(_supa(), nc)
    rows = book.select_policies(_supa(), nowcerts=nc)
    assert [r["policy_guid"] for r in rows] == ["g1"]


@pytest.mark.parametrize("flag,truthy", [
    (True, True), ("true", True), ("True", True), ("1", True),
    (False, False), ("false", False), ("0", False), (None, False),
])
def test_the_quote_flag_is_read_however_nowcerts_spells_it(flag, truthy):
    """The AMS is inconsistent about casing and about bool-vs-string."""
    assert book._is_quote({"isQuote": flag}) is truthy


def test_a_row_with_no_quote_flag_counts_as_a_policy():
    """Absence must not silently drop a real policy out of the book."""
    assert book._is_quote({"databaseId": "g1"}) is False


def test_alternate_spellings_of_the_flag_are_honoured():
    assert book._is_quote({"IsQuote": "true"}) is True
    assert book._is_quote({"is_quote": True}) is True
