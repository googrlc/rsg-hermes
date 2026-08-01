"""Tests for the NowCerts Opportunity → pipeline mirror."""
from __future__ import annotations

from typing import Any

from hermes_core import opportunities as opp
from hermes.sync import opportunity_sync as osync


class FakeSupa:
    def __init__(self, opps=None, policies=None):
        self.tables = {opp.TABLE: list(opps or []), "canonical_policies": list(policies or [])}
        self._n = 0

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if k == "order" or not isinstance(v, str):
                continue
            if v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
            elif v.startswith("in.(") and k == "nowcerts_insured_guid":
                wanted = set(v[len("in.("):-1].split(","))
                rows = [r for r in rows if str(r.get(k)) in wanted]
        return [dict(r) for r in rows][:limit]

    def insert(self, table, payload):
        self._n += 1
        row = {"id": f"opp-{self._n}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def update(self, table, rid, payload):
        for r in self.tables.get(table, []):
            if str(r.get("id")) == str(rid):
                r.update(payload)
                return dict(r)
        raise AssertionError("no row")


class FakeNC:
    def __init__(self, opps):
        self._o = opps

    def fetch_opportunities(self, *, page_size=100, max_pages=1000):
        return list(self._o)


def nc_opp(**kw):
    base = {
        "id": "NCO-1", "insuredDatabaseId": "ins1", "insuredCommercialName": "Jarah Group LLC",
        "lineOfBusinessName": "General Liability", "opportunityStageName": "Quotes Received",
        "neededBy": "2026-07-24T00:00:00-05:00", "winProbability": "VeryGood",
        "referralSourceName": "Website", "createdFromRenewal": False, "description": "GL new biz",
    }
    base.update(kw)
    return base


def test_new_opportunity_mirrored_verbatim():
    supa = FakeSupa()
    res = osync.run_opportunity_sync(FakeNC([nc_opp()]), supa)
    assert res.fetched == 1 and res.created == 1
    row = supa.tables[opp.TABLE][0]
    assert row["stage"] == "Quotes Received"                 # verbatim
    assert row["status"] == opp.STATUS_OPEN
    assert row["opportunity_type"] == "New Business"
    assert row["likelihood"] == "Very Good"                  # VeryGood → Very Good
    assert row["referral_source"] == "Website"
    assert row["nowcerts_opportunity_id"] == "NCO-1"
    assert row["effective_date"] == "2026-07-24"


def test_bound_won_and_renewal_type():
    supa = FakeSupa()
    osync.run_opportunity_sync(FakeNC([
        nc_opp(id="A", opportunityStageName="Bound / Won", createdFromRenewal=True, lineOfBusinessName="Personal Auto"),
        nc_opp(id="B", opportunityStageName="Lost", lineOfBusinessName="Homeowners"),
        nc_opp(id="C", opportunityStageName="Annual Policy Review", lineOfBusinessName="Commercial Auto"),
    ]), supa)
    rows = {r["nowcerts_opportunity_id"]: r for r in supa.tables[opp.TABLE]}
    assert rows["A"]["status"] == opp.STATUS_WON and rows["A"]["opportunity_type"] == "Renewals"
    assert rows["B"]["status"] == opp.STATUS_LOST
    assert rows["C"]["opportunity_type"] == "Renewals"       # 'Annual Policy Review' → renewal


def test_estimated_premium_from_canonical_book():
    supa = FakeSupa(policies=[
        {"nowcerts_insured_guid": "ins1", "lines_of_business": "General Liability", "active": True, "annualized_premium": 4200},
    ])
    osync.run_opportunity_sync(FakeNC([nc_opp()]), supa)
    assert supa.tables[opp.TABLE][0]["premium_estimate"] == 4200


def test_estimated_premium_falls_back_to_lob_average():
    # opp client has no GL policy → estimate = agency GL average (new-business case).
    supa = FakeSupa(policies=[
        {"nowcerts_insured_guid": "other1", "lines_of_business": "General Liability", "active": True, "annualized_premium": 3000},
        {"nowcerts_insured_guid": "other2", "lines_of_business": "General Liability", "active": True, "annualized_premium": 5000},
    ])
    osync.run_opportunity_sync(FakeNC([nc_opp(insuredDatabaseId="newclient")]), supa)
    assert supa.tables[opp.TABLE][0]["premium_estimate"] == 4000


def test_default_premium_env(monkeypatch):
    monkeypatch.setenv("HERMES_OPPORTUNITY_DEFAULT_PREMIUM", "1500")
    supa = FakeSupa()   # no canonical policy → default
    osync.run_opportunity_sync(FakeNC([nc_opp()]), supa)
    assert supa.tables[opp.TABLE][0]["premium_estimate"] == 1500


def test_resync_updates_by_nowcerts_id_not_duplicate():
    supa = FakeSupa()
    nc = FakeNC([nc_opp(opportunityStageName="Sent For Quoting")])
    osync.run_opportunity_sync(nc, supa)
    # stage advances in NowCerts; re-sync updates the same row
    nc2 = FakeNC([nc_opp(opportunityStageName="Bound / Won")])
    res = osync.run_opportunity_sync(nc2, supa)
    assert res.created == 0 and res.updated == 1
    assert len(supa.tables[opp.TABLE]) == 1
    assert supa.tables[opp.TABLE][0]["stage"] == "Bound / Won"


def test_adopts_existing_row_by_client_lob_type():
    # A real migrated row carries the full column set (so schema discovery sees them).
    existing = {c: None for c in osync._COLS}
    existing.update({
        "id": "opp-x", "client_identifier": opp.make_client_identifier("Jarah Group LLC"),
        "line_of_business": "General Liability", "opportunity_type": "New Business",
        "stage": "Preparing Application", "premium_estimate": 999,
    })
    supa = FakeSupa(opps=[existing])
    res = osync.run_opportunity_sync(FakeNC([nc_opp()]), supa)
    assert res.created == 0 and res.updated == 1
    row = supa.tables[opp.TABLE][0]
    assert row["nowcerts_opportunity_id"] == "NCO-1"          # stamped onto the existing row
    assert row["premium_estimate"] == 999                    # human estimate not clobbered


def test_sync_does_not_clobber_a_crm_worked_row():
    existing = {c: None for c in osync._COLS}
    existing.update({
        "id": "opp-x", "client_identifier": opp.make_client_identifier("Jarah Group LLC"),
        "line_of_business": "General Liability", "opportunity_type": "New Business",
        "nowcerts_opportunity_id": "NCO-1", "sync_source": "crm", "stage": "Sent For Quoting",
    })
    supa = FakeSupa(opps=[existing])
    res = osync.run_opportunity_sync(FakeNC([nc_opp(opportunityStageName="Bound / Won")]), supa)
    assert res.updated == 0 and res.skipped_worked == 1
    assert supa.tables[opp.TABLE][0]["stage"] == "Sent For Quoting"   # CRM working copy preserved


def test_skips_opportunity_without_client_or_lob():
    supa = FakeSupa()
    res = osync.run_opportunity_sync(FakeNC([nc_opp(insuredCommercialName="", insuredFirstName="", insuredLastName="")]), supa)
    assert res.skipped_no_client == 1 and res.created == 0
