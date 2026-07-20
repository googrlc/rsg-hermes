"""Tests for prompt AMS priming of a freshly created opportunity.

Covers the read-only pull from NowCerts (insured_id + segment fields + the
read-only referral_source) and the no-match / already-linked paths.
"""
from __future__ import annotations

from typing import Any

from hermes.intake import opportunity_priming as P


class FakeSupa:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, dict[str, Any]]] = []

    def update(self, table, rid, payload):
        self.updates.append((table, rid, payload))
        return {"id": rid, **payload}


class MatchNC:
    def __init__(self, insured: dict[str, Any]) -> None:
        self._i = insured

    def find_insured(self, **_kw):
        return self._i


class NoMatchNC:
    def find_insured(self, **_kw):
        return None


def test_matched_insured_links_and_pulls_referral_and_segment():
    nc = MatchNC({
        "id": "GUID-1", "prospectType": "Hot_Prospect", "insuredType": "Commercial",
        "referralSourceName": "Referral: Bob",
    })
    supa = FakeSupa()
    opps = [{"id": "o1", "insured_name": "Acme LLC", "client_identifier": "acme-llc:123", "line_of_business": "GL"}]
    res = P.prime_new_opportunities(supa, opps, nc=nc)
    assert res["matched"] and res["linked"] == 1
    payload = supa.updates[0][2]
    assert payload["insured_id"] == "GUID-1"
    assert payload["referral_source"] == "Referral: Bob"      # read-only pull
    assert payload["prospect_type"] == "Hot_Prospect"
    assert payload["insured_type"] == "Commercial"


def test_one_lookup_applies_to_all_client_rows():
    nc = MatchNC({"id": "GUID-2", "referralSourceName": "Trade Show"})
    supa = FakeSupa()
    opps = [
        {"id": "o1", "insured_name": "Beta Inc", "client_identifier": "beta:1", "line_of_business": "GL"},
        {"id": "o2", "insured_name": "Beta Inc", "client_identifier": "beta:1", "line_of_business": "WC"},
    ]
    res = P.prime_new_opportunities(supa, opps, nc=nc)
    assert res["linked"] == 2
    assert {u[2]["insured_id"] for u in supa.updates} == {"GUID-2"}
    assert all(u[2]["referral_source"] == "Trade Show" for u in supa.updates)


def test_no_match_without_kick_is_noop():
    res = P.prime_new_opportunities(
        FakeSupa(),
        [{"id": "o1", "insured_name": "New Co", "client_identifier": "new-co"}],
        nc=NoMatchNC(), kick_executor=False,
    )
    assert not res["matched"] and res["linked"] == 0 and not res["kicked"]


def test_already_linked_row_skipped():
    res = P.prime_new_opportunities(FakeSupa(), [{"id": "o1", "insured_id": "x"}], nc=MatchNC({}))
    assert res["linked"] == 0 and not res["matched"]
