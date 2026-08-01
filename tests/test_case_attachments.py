"""Case attachments (issue #195, second half).

Nextcloud stores the bytes; agency_crm_document_links stores a pointer. Reuses
the same file_document -> link_document path the renewal PDF filer already uses,
so a hand-attached document lands in the same client folder tree as a generated
one instead of a parallel store.

Case-level only, deliberately: every task belongs to a case or a client, so a
second home for documents would just be somewhere for them to hide.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from hermes_app import deps


CASE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "case_number": "SER-20260726-AAAA",
    "case_type": "service",
    "insured_name": "Acme Trucking",
    "insured_database_id": "22222222-2222-2222-2222-222222222222",
}
USER = "lamar@risksolutionsgroup.net"


class FakeSupa:
    def __init__(self, case=CASE):
        self.case = case
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, str, dict]] = []

    def select(self, table, *, columns="*", params=None, limit=1000):
        if table == "agency_crm_cases":
            return [dict(self.case)] if self.case else []
        if table == "agency_crm_users":
            return [{"email": USER, "display_name": "Lamar", "active": True}]
        return []

    def insert(self, table, payload):
        self.inserts.append((table, dict(payload)))
        return {"id": "doc-1", **payload}

    def update(self, table, record_id, payload):
        self.updates.append((table, record_id, dict(payload)))
        return {"id": record_id, **payload}


class FakeNextcloud:
    instances: list["FakeNextcloud"] = []

    def __init__(self, *a, **k):
        self.calls: list[dict] = []
        FakeNextcloud.instances.append(self)

    def file_document(self, **kw):
        self.calls.append(kw)
        client = kw.get("client")
        if client:
            rel = f"Clients/{client}/{kw['category']}/{kw['filename']}"
        else:
            rel = f"Internal/{kw.get('internal_folder') or 'General'}/{kw['filename']}"
        return {"path": rel, "url": f"https://nc.example/{rel}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HERMES_API_TOKEN", "")
    FakeNextcloud.instances.clear()
    supa = FakeSupa()
    import hermes.api as api_mod
    import hermes_integrations.nextcloud_client as nc_mod

    monkeypatch.setattr(deps, "get_supa", lambda: supa)
    monkeypatch.setattr(nc_mod, "NextcloudClient", FakeNextcloud)
    return TestClient(api_mod.app), supa


def _post(c, *, filename="dec-page.pdf", content=b"%PDF-1.4 fake", **form):
    data = {"uploaded_by": USER, **form}
    return c.post(
        f"/api/cases/{CASE['id']}/documents",
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
        data=data,
    )


# --- the happy path ----------------------------------------------------------

def test_a_file_is_stored_in_nextcloud_and_linked_in_the_crm(client):
    c, supa = client
    r = _post(c)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["filed_to"] == "Clients/Acme Trucking/Correspondence/dec-page.pdf"

    table, row = supa.inserts[-1]
    assert table == "agency_crm_document_links"
    assert row["case_id"] == CASE["id"]
    assert row["nextcloud_path"] == body["filed_to"]
    assert row["nextcloud_url"].startswith("https://nc.example/")
    assert row["uploaded_by_email"] == USER
    # The insured is carried across so the document is reachable client-side too.
    assert row["insured_database_id"] == CASE["insured_database_id"]


def test_no_file_bytes_are_written_to_the_database(client):
    """Nextcloud is the store. A pointer row must not carry the payload."""
    c, supa = client
    _post(c, content=b"%PDF-1.4 " + b"x" * 5000)
    _, row = supa.inserts[-1]
    assert not any(isinstance(v, (bytes, bytearray)) for v in row.values())
    assert all(len(str(v)) < 500 for v in row.values())


def test_the_title_defaults_to_the_filename(client):
    c, supa = client
    _post(c)
    assert supa.inserts[-1][1]["title"] == "dec-page.pdf"


def test_an_explicit_title_is_kept(client):
    c, supa = client
    _post(c, title="Signed dec page")
    assert supa.inserts[-1][1]["title"] == "Signed dec page"


def test_the_case_folder_pointer_is_refreshed(client):
    c, supa = client
    _post(c)
    table, rid, payload = supa.updates[-1]
    assert (table, rid) == ("agency_crm_cases", CASE["id"])
    assert payload["nextcloud_folder_url"].startswith("https://nc.example/")


# --- filing destination ------------------------------------------------------

@pytest.mark.parametrize("case_type,folder", [
    ("renewal", "Renewal Reviews"),
    ("marketing", "Quotes"),
    ("service", "Correspondence"),
    ("something-new", "Correspondence"),
])
def test_the_folder_follows_the_case_type(client, case_type, folder):
    """A renewal's paperwork belongs in Renewal Reviews, not a generic dump."""
    c, supa = client
    supa.case = {**CASE, "case_type": case_type}
    r = _post(c)
    assert f"/{folder}/" in r.json()["filed_to"]


def test_an_explicit_category_overrides_the_case_type(client):
    c, _ = client
    r = _post(c, category="COIs")
    assert "/COIs/" in r.json()["filed_to"]


def test_an_unknown_category_is_rejected_by_name(client):
    c, _ = client
    r = _post(c, category="Random Folder")
    assert r.status_code == 400
    assert "COIs" in r.json()["detail"]


def test_a_case_with_no_insured_files_internally(client):
    """Guessing a client name would misfile it under somebody."""
    c, supa = client
    supa.case = {**CASE, "insured_name": None}
    r = _post(c)
    assert r.json()["filed_to"] == "Internal/Case Files/dec-page.pdf"


# --- refusals ----------------------------------------------------------------

def test_an_unknown_case_is_a_404(client):
    c, supa = client
    supa.case = None
    r = _post(c)
    assert r.status_code == 404


def test_an_empty_file_is_rejected(client):
    c, _ = client
    r = _post(c, content=b"")
    assert r.status_code == 400
    assert "empty" in r.json()["detail"]


def test_an_oversized_file_is_rejected_with_the_limit(client):
    c, _ = client
    from hermes.routers import cases as cases_router

    r = _post(c, content=b"x" * (cases_router._CASE_DOC_MAX_BYTES + 1))
    assert r.status_code == 413
    assert "limit is 25MB" in r.json()["detail"]


def test_an_unknown_uploader_is_rejected(client):
    """uploaded_by_email is a real FK to agency_crm_users."""
    c, _ = client
    r = _post(c, uploaded_by="nobody@example.com")
    assert r.status_code == 400
    assert "agency_crm_users" in r.json()["detail"]


def test_a_link_failure_says_the_file_is_already_stored(client):
    """'Upload failed' would send someone hunting for a file that is right there."""
    c, supa = client

    def boom(table, payload):
        raise RuntimeError("postgrest down")

    supa.insert = boom
    r = _post(c)
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "File stored at" in detail and "Clients/Acme Trucking" in detail


def test_a_folder_pointer_failure_does_not_fail_the_upload(client):
    c, supa = client

    def boom(table, rid, payload):
        raise RuntimeError("update failed")

    supa.update = boom
    assert _post(c).status_code == 200
