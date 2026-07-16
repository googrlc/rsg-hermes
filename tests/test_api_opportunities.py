"""Tests for the opportunity create/list/search API endpoints (Retool cockpit)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app


@pytest.fixture(autouse=True)
def _reset_singletons():
    import hermes.api as api_mod
    api_mod._supa = None
    yield
    api_mod._supa = None


@pytest.fixture
def client():
    return TestClient(app)


class FakeSupa:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self._n = 0

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
            elif isinstance(v, str) and v.startswith("ilike."):
                pat = v[len("ilike."):].strip("*").lower()
                rows = [r for r in rows if pat in str(r.get(k) or "").lower()]
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
        return {}


def test_create_opportunity_from_name(client):
    supa = FakeSupa()
    with patch("hermes.api._get_supa", return_value=supa):
        r = client.post("/api/opportunities", json={
            "insured_name": "Acme LLC", "line_of_business": "General Liability",
            "stage": "Quoted", "premium_estimate": 1500,
        })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["created"] is True
    opp = body["opportunity"]
    assert opp["client_identifier"] == "acme-llc"
    assert opp["line_of_business"] == "General Liability"
    assert opp["stage"] == "Quoted"


def test_cross_sell_on_existing_client_creates_separate(client):
    supa = FakeSupa({"opportunities": [
        {"id": "o1", "client_identifier": "acme-llc", "line_of_business": "General Liability", "stage": "Bound"}
    ]})
    with patch("hermes.api._get_supa", return_value=supa):
        r = client.post("/api/opportunities", json={
            "insured_name": "Acme LLC", "line_of_business": "Commercial Auto",
            "insured_id": "guid-123",
        })
    assert r.status_code == 200 and r.json()["created"] is True
    assert len(supa.tables["opportunities"]) == 2          # GL + new Commercial Auto


def test_create_is_idempotent(client):
    supa = FakeSupa({"opportunities": [
        {"id": "o1", "client_identifier": "acme-llc", "line_of_business": "General Liability", "stage": "Bound"}
    ]})
    with patch("hermes.api._get_supa", return_value=supa):
        r = client.post("/api/opportunities", json={
            "insured_name": "Acme LLC", "line_of_business": "General Liability",
        })
    assert r.status_code == 200 and r.json()["created"] is False
    assert r.json()["opportunity"]["id"] == "o1"
    assert len(supa.tables["opportunities"]) == 1          # no duplicate


def test_missing_client_is_422(client):
    r = client.post("/api/opportunities", json={"line_of_business": "General Liability"})
    assert r.status_code == 422


def test_unknown_stage_is_400(client):
    supa = FakeSupa()
    with patch("hermes.api._get_supa", return_value=supa):
        r = client.post("/api/opportunities", json={
            "insured_name": "Acme LLC", "line_of_business": "GL", "stage": "Bogus",
        })
    assert r.status_code == 400


def test_list_opportunities(client):
    supa = FakeSupa({"opportunities": [
        {"id": "o1", "client_identifier": "a", "line_of_business": "GL", "stage": "New", "status": "open"},
    ]})
    with patch("hermes.api._get_supa", return_value=supa):
        r = client.get("/api/opportunities")
    assert r.status_code == 200 and r.json()["count"] == 1


def test_client_search_matches_book(client):
    supa = FakeSupa({"canonical_clients": [
        {"nowcerts_insured_guid": "g1", "insured_name": "Acme LLC", "client_type": "Commercial"},
        {"nowcerts_insured_guid": "g2", "insured_name": "Beta Inc", "client_type": "Commercial"},
    ]})
    with patch("hermes.api._get_supa", return_value=supa):
        r = client.get("/api/clients/search", params={"q": "acme"})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert r.json()["clients"][0]["nowcerts_insured_guid"] == "g1"


def test_client_search_short_query_returns_empty(client):
    r = client.get("/api/clients/search", params={"q": "a"})
    assert r.status_code == 200 and r.json()["count"] == 0
