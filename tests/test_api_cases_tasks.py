"""Tests for the case/task/agency-user API endpoints (Retool cockpit, FK-guarded)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app


@pytest.fixture(autouse=True)
def _reset_singletons():
    from hermes_app import deps
    deps.reset_clients()
    yield
    deps.reset_clients()


@pytest.fixture
def client():
    return TestClient(app)


USERS = [
    {"email": "gretchen@risksolutionsgroup.net", "display_name": "Gretchen", "role": "csr", "active": True},
    {"email": "lamar@risksolutionsgroup.net", "display_name": "Lamar", "role": "administrator", "active": True},
    # The service account. _service_email() defaults here, so it must be a valid
    # FK target or every machine-attributed create 400s.
    {"email": "lc-rsg@risksolutionsgroup.net", "display_name": "RSG Service", "role": "service", "active": True},
]


class FakeSupa:
    def __init__(self, tables=None):
        self.tables = dict(tables or {})
        self.tables.setdefault("agency_crm_users", list(USERS))
        self.updates: list[tuple] = []
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

    def update(self, table, record_id, payload):
        self.updates.append((table, record_id, dict(payload)))
        for r in self.tables.get(table, []):
            if str(r.get("id")) == str(record_id):
                r.update(payload)
                return dict(r)
        return {"id": record_id, **payload}


def _patch(supa):
    return patch("hermes_app.deps.get_supa", return_value=supa)


# --- agency users ------------------------------------------------------------
def test_agency_users_lists_active(client):
    with _patch(FakeSupa()):
        r = client.get("/api/agency-users")
    # Both humans plus the service account — all three are valid FK targets.
    assert r.status_code == 200 and r.json()["count"] == 3


def test_assignable_excludes_the_service_account(client):
    """Offering "RSG Service" in an assignee dropdown invites someone to assign
    real work to a robot. It stays valid for created_by / approved_by."""
    with _patch(FakeSupa()):
        r = client.get("/api/agency-users?assignable=true")
    emails = [u["email"] for u in r.json()["users"]]
    assert r.json()["count"] == 2
    assert "lc-rsg@risksolutionsgroup.net" not in emails
    assert "lamar@risksolutionsgroup.net" in emails


# --- cases -------------------------------------------------------------------
def test_create_case_ok(client):
    supa = FakeSupa()
    with _patch(supa):
        r = client.post("/api/cases", json={
            "title": "COI request — Acme", "case_type": "service",
            "owner_email": "gretchen@risksolutionsgroup.net",
            "created_by_email": "lamar@risksolutionsgroup.net",
            "insured_name": "Acme LLC",
            "insured_database_id": "11111111-2222-3333-4444-555555555555",
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
            "insured_database_id": "11111111-2222-3333-4444-555555555555",
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


# --- the insured a case cannot reach the AMS without --------------------------
# 12 of 13 cases on this system had no insured_database_id, so no task on them
# could ever be pushed: NowCerts attaches a task to a client. Optional-and-absent
# made that invisible until push time, long after whoever opened the case had
# moved on.

def test_a_case_cannot_be_opened_without_its_insured(client):
    supa = FakeSupa()
    with _patch(supa):
        r = client.post("/api/cases", json={
            "title": "COI request", "case_type": "service",
            "owner_email": "gretchen@risksolutionsgroup.net",
        })
    assert r.status_code == 422, r.text
    assert "insured_database_id" in r.text


def test_nothing_is_written_when_the_insured_is_missing(client):
    """A refused create must not leave a half-made case or a stray event."""
    supa = FakeSupa()
    with _patch(supa):
        client.post("/api/cases", json={
            "title": "COI request", "owner_email": "gretchen@risksolutionsgroup.net",
        })
    assert supa.tables.get("agency_crm_cases", []) == []
    assert supa.tables.get("agency_crm_case_events", []) == []


def test_an_existing_case_can_be_linked_to_its_insured_afterwards(client):
    """The cases that predate the rule need a route to becoming pushable, and a
    case opened before the client was known needs one too."""
    supa = FakeSupa({"agency_crm_cases": [
        {"id": "c1", "title": "A", "status": "open", "case_type": "service"},
    ]})
    with _patch(supa):
        r = client.patch("/api/cases/c1", json={
            "insured_database_id": "11111111-2222-3333-4444-555555555555",
        })
    assert r.status_code == 200, r.text
    assert any(
        u[2].get("insured_database_id") == "11111111-2222-3333-4444-555555555555"
        for u in supa.updates
    ), supa.updates
