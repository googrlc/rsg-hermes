"""Tests for cross-sell search over the canonical mirror."""
from __future__ import annotations

from typing import Any

from hermes import cross_sell


class FakeSupa:
    def __init__(self, clients, policies):
        self._clients = clients
        self._policies = policies
        self.calls: list[dict[str, Any]] = []

    def select(self, table, *, columns="*", params=None, limit=100):
        self.calls.append({"table": table, "params": params})
        if table == "canonical_clients":
            want = (params or {}).get("insured_name", "")
            frag = want[len("ilike.*"):-1].lower() if want.startswith("ilike.*") else ""
            return [c for c in self._clients if frag in (c.get("insured_name") or "").lower()][:limit]
        if table == "canonical_policies":
            inp = (params or {}).get("nowcerts_insured_guid", "")
            guids = inp[len("in.("):-1].split(",") if inp.startswith("in.(") else []
            return [p for p in self._policies if p.get("nowcerts_insured_guid") in guids]
        return []


CLIENTS = [
    {"nowcerts_insured_guid": "g1", "insured_name": "Acme LLC", "client_type": "Commercial"},
    {"nowcerts_insured_guid": "g2", "insured_name": "Acme Plumbing", "client_type": "Commercial"},
    {"nowcerts_insured_guid": "g3", "insured_name": "Beta Inc", "client_type": "Commercial"},
]
POLICIES = [
    {"nowcerts_insured_guid": "g1", "lines_of_business": "General Liability", "active": True, "annualized_premium": 1000},
    {"nowcerts_insured_guid": "g1", "lines_of_business": "Commercial Auto", "active": True, "premium_amount": 500},
    {"nowcerts_insured_guid": "g1", "lines_of_business": "Workers Comp", "active": False, "annualized_premium": 9999},  # inactive → ignored
]


def test_search_matches_by_name_and_rolls_up_active_policies():
    supa = FakeSupa(CLIENTS, POLICIES)
    out = cross_sell.search_cross_sell(supa, query="acme")
    assert out["count"] == 2                                  # Acme LLC + Acme Plumbing
    acme = next(c for c in out["clients"] if c["client_name"] == "Acme LLC")
    assert acme["insured_id"] == "g1"
    assert acme["current_lobs"] == ["Commercial Auto", "General Liability"]
    assert acme["active_policy_count"] == 2                    # inactive excluded
    assert acme["active_premium"] == 1500


def test_empty_query_returns_nothing_and_hits_no_tables():
    supa = FakeSupa(CLIENTS, POLICIES)
    out = cross_sell.search_cross_sell(supa, query="   ")
    assert out["count"] == 0 and out["clients"] == []
    assert supa.calls == []                                    # short-circuits before any read


def test_client_with_no_active_policies():
    supa = FakeSupa(CLIENTS, POLICIES)
    out = cross_sell.search_cross_sell(supa, query="beta")
    beta = out["clients"][0]
    assert beta["current_lobs"] == [] and beta["active_premium"] == 0
