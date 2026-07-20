"""Tests for the Leads feed (live NowCerts prospects)."""
from __future__ import annotations

from hermes import leads


class FakeNC:
    def __init__(self, insureds):
        self._i = insureds

    def fetch_insureds(self, *, page_size=100, since=None, max_pages=1000):
        return list(self._i)


def ins(**kw):
    base = {"id": "g1", "commercialName": "Acme LLC", "prospectType": "Hot_Prospect",
            "insuredType": "Commercial", "eMail": "a@acme.com"}
    base.update(kw)
    return base


def test_only_prospects_returned():
    nc = FakeNC([
        ins(id="g1", commercialName="Prospect Co", prospectType="Prospect"),
        ins(id="g2", commercialName="Active Client", prospectType=None),      # not a lead
        ins(id="g3", commercialName="Hot Co", prospectType="Hot_Prospect"),
        ins(id="g4", commercialName="", prospectType="Cold_Prospect", firstName="", lastName=""),  # no name → skip
    ])
    out = leads.list_prospects(nc)
    names = {l["name"] for l in out["leads"]}
    assert names == {"Prospect Co", "Hot Co"}
    assert out["count"] == 2


def test_lead_fields_mapped():
    nc = FakeNC([ins(id="g9", commercialName="Beta Inc", prospectType="Prospect",
                     leadSources="Referral", phone="555-1212", insuredType="Commercial")])
    lead = leads.list_prospects(nc)["leads"][0]
    assert lead["insured_id"] == "g9"
    assert lead["name"] == "Beta Inc"
    assert lead["prospect_type"] == "Prospect"
    assert lead["lead_source"] == "Referral"
    assert lead["phone"] == "555-1212"


def test_personal_prospect_name_from_first_last():
    nc = FakeNC([{"id": "p1", "firstName": "Jane", "lastName": "Roe", "prospectType": "Prospect"}])
    assert leads.list_prospects(nc)["leads"][0]["name"] == "Jane Roe"


def test_limit_caps_leads():
    nc = FakeNC([ins(id=f"g{i}", commercialName=f"Co {i}", prospectType="Prospect") for i in range(10)])
    out = leads.list_prospects(nc, limit=3)
    assert out["count"] == 3
