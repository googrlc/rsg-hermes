"""Tests for the case/task/agency-user API endpoints (Retool cockpit, FK-guarded)."""
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


USERS = [
    {"email": "gretchen@risksolutionsgroup.net", "display_name": "Gretchen", "role": "csr", "active": True},
    {"email": "lamar@risksolutionsgroup.net", "display_name": "Lamar", "role": "administrator", "active": True},
]


class FakeSupa:
    def __init__(self, tables=None):
        self.tables = dict(tables or {})
        self.tables.setdefault("agency_crm_users", list(USERS))
        self._n = 0

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                want = v[3:]
                rows = [r for r in rows if str(r.get(k)).lower() == want.lower()]
        return [dict(r) for r in rows][:limit]

    def insert(self, table, payload):
        self._n += 1
        row = {"id": f"{table[:3]}-{self._n}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)


def _patch(supa):
    return patch("hermes.api._get_supa", return_value=supa)


# --- agency users ------------------------------------------------------------
def test_agency_users_lists_active(client):
    with _patch(FakeSupa()):
        r = client.get("/api/agency-users")
    assert r.status_code == 200 and r.json()["count"] == 2


# --- cases -------------------------------------------------------------------
def test_create_case_ok(client):
    supa = FakeSupa()
    with _patch(supa):
        r = client.post("/api/cases", json={
            "title": "COI request — Acme", "case_type": "service",
            "owner_email": "gretchen@risksolutionsgroup.net",
            "created_by_email": "lamar@risksolutionsgroup.net",
            "insured_name": "Acme LLC",
        })
    assert r.status_code == 200
    case = r.json()["case"]
    assert case["title"] == "COI request — Acme"
    assert case["status"] == "open"
    assert case["case_number"].startswith("SER-")           # service → SER-YYYYMMDD-XXXXXX
    # a case_created event was logged
    assert any(e.get("event_type") == "case_created" for e in supa.tables.get("agency_crm_case_events", []))


def test_create_case_rejects_unknown_owner(client):
    supa = FakeSupa()
    with _patch(supa):
        r = client.post("/api/cases", json={
            "title": "x", "owner_email": "lamar@risk-solutionsgroup.com",  # .com — not in users
        })
    assert r.status_code == 400
    assert "agency_crm_users" in r.json()["detail"]


def test_list_cases(client):
    supa = FakeSupa({"agency_crm_cases": [
        {"id": "c1", "title": "A", "status": "open", "case_type": "service", "created_at": "2026-07-16"},
    ]})
    with _patch(supa):
        r = client.get("/api/cases")
    assert r.status_code == 200 and r.json()["count"] == 1


# --- tasks -------------------------------------------------------------------
def test_create_task_ok(client):
    supa = FakeSupa({"agency_crm_cases": [{"id": "case-1", "title": "A"}]})
    with _patch(supa):
        r = client.post("/api/tasks", json={
            "case_id": "case-1", "title": "Call the client",
            "assigned_to_email": "gretchen@risksolutionsgroup.net",
            "created_by_email": "lamar@risksolutionsgroup.net",
        })
    assert r.status_code == 200 and r.json()["created"] is True
    assert r.json()["task"]["title"] == "Call the client"
    assert r.json()["task"]["case_id"] == "case-1"


def test_create_task_rejects_unknown_assignee(client):
    supa = FakeSupa({"agency_crm_cases": [{"id": "case-1"}]})
    with _patch(supa):
        r = client.post("/api/tasks", json={
            "case_id": "case-1", "title": "x",
            "assigned_to_email": "nobody@example.com",
        })
    assert r.status_code == 400


def test_create_task_idempotent_on_title(client):
    supa = FakeSupa({
        "agency_crm_cases": [{"id": "case-1"}],
        "agency_crm_tasks": [{"id": "t0", "case_id": "case-1", "title": "Call the client"}],
    })
    with _patch(supa):
        r = client.post("/api/tasks", json={
            "case_id": "case-1", "title": "Call the client",
            "assigned_to_email": "gretchen@risksolutionsgroup.net",
        })
    assert r.status_code == 200 and r.json()["created"] is False


def test_list_tasks_scoped_to_case(client):
    supa = FakeSupa({"agency_crm_tasks": [
        {"id": "t1", "case_id": "case-1", "title": "A", "created_at": "2026-07-16"},
        {"id": "t2", "case_id": "case-2", "title": "B", "created_at": "2026-07-16"},
    ]})
    with _patch(supa):
        r = client.get("/api/tasks", params={"case_id": "case-1"})
    assert r.status_code == 200 and r.json()["count"] == 1
