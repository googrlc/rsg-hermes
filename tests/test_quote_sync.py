"""Tests for the NowCerts quotes → opportunities pipeline sync."""
from __future__ import annotations

from typing import Any

from hermes.intake import opportunities as opp
from hermes.sync import quote_sync as qs


# --- fakes -------------------------------------------------------------------
class FakeSupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = tables or {}
        self._n = 0

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                want = v[3:]
                rows = [r for r in rows if str(r.get(k)) == want]
        return [dict(r) for r in rows][:limit]

    def insert(self, table, payload):
        self._n += 1
        row = {"id": f"opp-{self._n}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def update(self, table, record_id, payload):
        for r in self.tables.get(table, []):
            if str(r.get("id")) == str(record_id):
                r.update(payload)
                return dict(r)
        raise AssertionError(f"update: no {table} row {record_id}")


class FakeNowCerts:
    def __init__(self, policies=None) -> None:
        self._policies = policies or []

    def fetch_policies(self, *, since=None, page_size=100, max_pages=1000):
        return list(self._policies)


def nc_quote(number="Q1", *, insured="ins1", name="Acme LLC", lob="General Liability",
             premium=1200.0, carrier="Acme Mutual", is_quote=True, itype="Commercial",
             fein=None, guid=None):
    return {
        "isQuote": is_quote, "databaseId": guid or f"qg-{number}", "number": number,
        "insuredDatabaseId": insured, "insuredCommercialName": name,
        "lineOfBusinesses": [{"lineOfBusinessName": lob}], "totalPremium": premium,
        "carrierName": carrier, "insuredType": itype, "insuredFEIN": fein,
    }


def run(supa, nc, **kw):
    return qs.run_quote_sync(nc, supa, **kw)


# --- tests -------------------------------------------------------------------
def test_new_quote_creates_opportunity_and_links_ids():
    supa, nc = FakeSupabase(), FakeNowCerts(policies=[nc_quote("Q1")])
    res = run(supa, nc)
    assert res.quotes_fetched == 1 and res.created == 1 and res.linked == 0
    row = supa.tables[opp.TABLE][0]
    assert row["stage"] == opp.STAGE_QUOTES_RECEIVED and row["status"] == opp.STATUS_OPEN
    assert row["insured_name"] == "Acme LLC"
    assert row["line_of_business"] == "General Liability"
    assert row["premium_estimate"] == 1200.0
    assert row["carrier"] == "Acme Mutual"
    assert row["source"] == "nowcerts_quote_sync"
    # NowCerts identifiers backfilled via link_nowcerts
    assert row["quote_number"] == "Q1"
    assert row["nowcerts_quote_guid"] == "qg-Q1"
    assert row["insured_id"] == "ins1"


def test_non_quote_policy_ignored():
    supa, nc = FakeSupabase(), FakeNowCerts(policies=[nc_quote("P1", is_quote=False)])
    res = run(supa, nc)
    assert res.quotes_fetched == 0 and res.created == 0
    assert supa.tables.get(opp.TABLE, []) == []


def test_existing_opportunity_links_without_resetting_stage():
    ci = opp.make_client_identifier("Acme LLC", None)
    supa = FakeSupabase({opp.TABLE: [{
        "id": "opp-x", "client_identifier": ci, "line_of_business": "General Liability",
        "opportunity_type": opp.TYPE_NEW_BUSINESS,
        "stage": opp.STAGE_BOUND, "status": opp.STATUS_WON,
    }]})
    nc = FakeNowCerts(policies=[nc_quote("Q1", name="Acme LLC", lob="General Liability")])
    res = run(supa, nc)
    assert res.created == 0 and res.linked == 1
    row = supa.tables[opp.TABLE][0]
    assert row["stage"] == opp.STAGE_BOUND        # human-advanced stage preserved
    assert row["status"] == opp.STATUS_WON
    assert row["quote_number"] == "Q1"            # ids still backfilled
    assert row["nowcerts_quote_guid"] == "qg-Q1"


def test_quote_missing_name_or_lob_skipped():
    supa = FakeSupabase()
    nc = FakeNowCerts(policies=[
        nc_quote("Q1", name=""),                       # no insured name
        nc_quote("Q2", lob=None),                       # no LOB
    ])
    res = run(supa, nc)
    assert res.skipped_incomplete == 2 and res.created == 0


def test_dry_run_counts_without_writing():
    supa = FakeSupabase()
    nc = FakeNowCerts(policies=[
        nc_quote("Q1", name="Acme LLC"),
        nc_quote("Q2", name="Beta Inc", lob="Commercial Auto"),
    ])
    res = run(supa, nc, dry_run=True)
    assert res.created == 2 and res.linked == 0
    assert supa.tables.get(opp.TABLE, []) == []


def test_dry_run_classifies_existing_as_linked():
    ci = opp.make_client_identifier("Acme LLC", None)
    supa = FakeSupabase({opp.TABLE: [{
        "id": "opp-x", "client_identifier": ci, "line_of_business": "General Liability",
        "stage": opp.STAGE_SENT_QUOTING,
    }]})
    nc = FakeNowCerts(policies=[nc_quote("Q1", name="Acme LLC", lob="General Liability")])
    res = run(supa, nc, dry_run=True)
    assert res.linked == 1 and res.created == 0


def test_limit_caps_quotes():
    supa = FakeSupabase()
    nc = FakeNowCerts(policies=[nc_quote(f"Q{i}", name=f"Co {i}") for i in range(4)])
    res = run(supa, nc, limit=2)
    assert res.quotes_fetched == 2


def test_new_quote_stamps_live_terms():
    """A new quote lands its real terms (premium_actual, dates, status) on the row."""
    q = nc_quote("Q1", premium=2500.0, carrier="Travelers")
    q["effectiveDate"] = "2026-08-01T00:00:00"
    q["expirationDate"] = "2027-08-01T00:00:00"
    q["status"] = "Quoted"
    supa, nc = FakeSupabase(), FakeNowCerts(policies=[q])
    res = run(supa, nc)
    assert res.created == 1
    row = supa.tables[opp.TABLE][0]
    assert row["premium_actual"] == 2500.0
    assert row["effective_date"] == "2026-08-01"      # _strip_date drops the time
    assert row["expiration_date"] == "2027-08-01"
    assert row["policy_status"] == "Quoted"
    assert row["sync_source"] == "nowcerts_quote_sync"
    assert row["synced_at"]                            # timestamp stamped


def test_open_opportunity_promoted_forward_to_quoted():
    """A still-open (Preparing Application) opportunity is promoted forward when a quote arrives."""
    ci = opp.make_client_identifier("Acme LLC", None)
    supa = FakeSupabase({opp.TABLE: [{
        "id": "opp-x", "client_identifier": ci, "line_of_business": "General Liability",
        "opportunity_type": opp.TYPE_NEW_BUSINESS,
        "stage": opp.STAGE_PREP, "status": opp.STATUS_OPEN, "premium_actual": None,
        "effective_date": None, "expiration_date": None, "policy_status": None,
        "synced_at": None, "sync_source": None, "insured_id": None,
        "quote_number": None, "nowcerts_quote_guid": None, "carrier": None,
    }]})
    nc = FakeNowCerts(policies=[nc_quote("Q1", name="Acme LLC", lob="General Liability")])
    res = run(supa, nc)
    assert res.created == 0 and res.linked == 1 and res.promoted == 1
    row = supa.tables[opp.TABLE][0]
    assert row["stage"] == opp.STAGE_QUOTES_RECEIVED   # promoted forward
    assert row["status"] == opp.STATUS_OPEN
    assert row["quote_number"] == "Q1"


def test_bound_opportunity_not_downgraded_or_promoted():
    """A Bound deal keeps its stage; promotion is forward-only."""
    ci = opp.make_client_identifier("Acme LLC", None)
    supa = FakeSupabase({opp.TABLE: [{
        "id": "opp-x", "client_identifier": ci, "line_of_business": "General Liability",
        "stage": opp.STAGE_BOUND, "status": opp.STATUS_WON, "insured_id": None,
        "quote_number": None, "nowcerts_quote_guid": None, "carrier": None,
    }]})
    nc = FakeNowCerts(policies=[nc_quote("Q1", name="Acme LLC", lob="General Liability")])
    res = run(supa, nc)
    assert res.promoted == 0
    row = supa.tables[opp.TABLE][0]
    assert row["stage"] == opp.STAGE_BOUND and row["status"] == opp.STATUS_WON
