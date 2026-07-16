"""Tests for the NowCerts → Supabase canonical book sync.

Covers: client/mirror upsert, policy insert with client linkage, volatile refresh
with renewed_policy/lineage preservation, duplicate-row collapse, NowCerts-side
collapse, schema-adaptive column filtering, and dry-run no-op.
"""
from __future__ import annotations

from typing import Any

from hermes.sync import canonical_book_sync as cbs


# --- fakes -------------------------------------------------------------------
class FakeSupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = tables or {}
        self._n = 0

    def select(self, table, *, columns="*", params=None, limit=100):
        return [dict(r) for r in self.tables.get(table, [])][:limit]

    def insert(self, table, payload):
        self._n += 1
        row = {"id": f"{table[:3]}-{self._n}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def update(self, table, record_id, payload):
        for r in self.tables.get(table, []):
            if str(r.get("id")) == str(record_id):
                r.update(payload)
                return dict(r)
        raise AssertionError(f"update: no {table} row {record_id}")

    def delete(self, table, record_id):
        rows = self.tables.get(table, [])
        self.tables[table] = [r for r in rows if str(r.get("id")) != str(record_id)]


class FakeNowCerts:
    def __init__(self, insureds=None, policies=None) -> None:
        self._insureds = insureds or []
        self._policies = policies or []

    def fetch_insureds(self, *, since=None, page_size=100, max_pages=1000):
        return list(self._insureds)

    def fetch_policies(self, *, since=None, page_size=100, max_pages=1000):
        return list(self._policies)


# --- builders ----------------------------------------------------------------
def nc_insured(guid, name="Acme LLC", *, active=True, itype="Commercial", fein="12-3456789"):
    return {"id": guid, "commercialName": name, "insuredType": itype,
            "fein": fein, "active": active}


def nc_policy(number, insured_guid="ins1", *, status="Active", eff="2026-01-01",
              exp="2027-01-01", premium=1000.0, carrier="Acme Mutual", lob="General Liability"):
    return {
        "number": number, "databaseId": f"pg-{number}", "insuredDatabaseId": insured_guid,
        "status": status, "effectiveDate": eff, "expirationDate": exp,
        "totalPremium": premium, "carrierName": carrier,
        "lineOfBusinesses": [{"lineOfBusinessName": lob}],
    }


def run(supa, nc, **kw):
    return cbs.run_canonical_book_sync(nc, supa, **kw)


# --- client + mirror ---------------------------------------------------------
def test_new_insured_creates_client_and_mirror():
    supa = FakeSupabase()
    nc = FakeNowCerts(insureds=[nc_insured("ins1", "Acme LLC")])
    res = run(supa, nc)
    assert res.clients_created == 1 and res.mirror_written == 1
    client = supa.tables["canonical_clients"][0]
    assert client["nowcerts_insured_guid"] == "ins1"
    assert client["name"] == "Acme LLC"
    mirror = supa.tables[cbs.MIRROR_TABLE][0]
    assert mirror["insured_guid"] == "ins1" and mirror["active"] is True


def test_insured_without_guid_or_name_skipped():
    supa = FakeSupabase()
    nc = FakeNowCerts(insureds=[{"id": "", "commercialName": ""}, {"id": "x", "commercialName": ""}])
    res = run(supa, nc)
    assert res.clients_created == 0
    assert supa.tables.get("canonical_clients", []) == []


def test_existing_client_updates_not_duplicates():
    supa = FakeSupabase({"canonical_clients": [
        {"id": "c-1", "nowcerts_insured_guid": "ins1", "name": "Old Name"}
    ]})
    nc = FakeNowCerts(insureds=[nc_insured("ins1", "New Name")])
    res = run(supa, nc)
    assert res.clients_created == 0 and res.clients_updated == 1
    assert len(supa.tables["canonical_clients"]) == 1
    assert supa.tables["canonical_clients"][0]["name"] == "New Name"


# --- policy insert + linkage -------------------------------------------------
def test_new_policy_inserted_and_linked_to_client():
    supa = FakeSupabase()
    nc = FakeNowCerts(
        insureds=[nc_insured("ins1", "Acme LLC")],
        policies=[nc_policy("P1", "ins1", premium=2500.0)],
    )
    res = run(supa, nc)
    assert res.policies_created == 1 and res.policies_updated == 0
    pol = supa.tables[cbs.POLICIES_TABLE][0]
    assert pol["policy_number"] == "P1"
    assert pol["nowcerts_insured_guid"] == "ins1"
    assert pol["client_id"] == supa.tables["canonical_clients"][0]["id"]
    assert pol["annualized_premium"] == 2500.0 and pol["premium_amount"] == 2500.0
    assert pol["active"] is True
    assert pol["policy_guid"] == "pg-P1"


# --- volatile refresh + lineage preservation ---------------------------------
def _live_policy_row(**over):
    """A realistic canonical_policies row — every live column present (nulls allowed),
    mirroring what PostgREST returns and what column discovery introspects."""
    row = {
        "id": "pol-1", "policy_number": "P1", "policy_guid": "pg-P1",
        "nowcerts_insured_guid": "ins1", "client_id": None, "renewed_policy": None,
        "line_of_business": "General Liability", "carrier": "Old Carrier",
        "status": "Active", "active": True,
        "effective_date": "2025-09-01", "expiration_date": "2026-09-01",
        "annualized_premium": 1000.0, "current_term_amount": 1000.0, "premium_amount": 1000.0,
        "raw_payload": {}, "synced_at": "2026-06-10T00:00:00Z", "updated_at": "2026-06-10T00:00:00Z",
    }
    row.update(over)
    return row


def test_existing_policy_refreshes_volatile_and_preserves_lineage():
    supa = FakeSupabase({cbs.POLICIES_TABLE: [
        _live_policy_row(renewed_policy="P0", client_id="c-existing")
    ]})
    nc = FakeNowCerts(policies=[nc_policy("P1", "ins9", status="Renewing",
                                          exp="2027-09-01", premium=3300.0)])
    res = run(supa, nc)
    assert res.policies_updated == 1 and res.policies_created == 0
    pol = supa.tables[cbs.POLICIES_TABLE][0]
    # lineage + client linkage preserved (never overwritten)
    assert pol["renewed_policy"] == "P0"
    assert pol["client_id"] == "c-existing"
    # volatile fields refreshed from live NowCerts
    assert pol["status"] == "Renewing"
    assert pol["expiration_date"] == "2027-09-01"
    assert pol["premium_amount"] == 3300.0


def test_empty_client_id_backfilled_when_resolvable():
    supa = FakeSupabase({cbs.POLICIES_TABLE: [{
        "id": "pol-1", "policy_number": "P1", "renewed_policy": None,
        "client_id": None, "status": "Active",
        "expiration_date": "2026-09-01", "synced_at": "2026-06-10T00:00:00Z",
    }]})
    nc = FakeNowCerts(
        insureds=[nc_insured("ins1", "Acme LLC")],
        policies=[nc_policy("P1", "ins1")],
    )
    run(supa, nc)
    pol = next(p for p in supa.tables[cbs.POLICIES_TABLE] if p["policy_number"] == "P1")
    assert pol["client_id"] == supa.tables["canonical_clients"][0]["id"]


# --- duplicate collapse ------------------------------------------------------
def test_duplicate_policy_rows_collapsed_to_status_keeper():
    supa = FakeSupabase({cbs.POLICIES_TABLE: [
        {"id": "keep", "policy_number": "D1", "status": "Active",
         "expiration_date": "2027-01-01", "synced_at": "2026-06-10T00:00:00Z",
         "renewed_policy": "D0", "client_id": "c1"},
        {"id": "loser", "policy_number": "D1", "status": "Renewed",
         "expiration_date": "2026-01-01", "synced_at": "2026-06-10T00:00:00Z",
         "renewed_policy": None, "client_id": None},
    ]})
    nc = FakeNowCerts(policies=[nc_policy("D1", "ins1", status="Active")])
    res = run(supa, nc)
    assert res.dup_rows_collapsed == 1
    remaining = [r for r in supa.tables[cbs.POLICIES_TABLE] if r["policy_number"] == "D1"]
    assert len(remaining) == 1 and remaining[0]["id"] == "keep"
    assert remaining[0]["renewed_policy"] == "D0"  # keeper lineage retained


def test_nowcerts_side_collapse_prefers_active():
    supa = FakeSupabase()
    nc = FakeNowCerts(policies=[
        nc_policy("X1", "ins1", status="Renewed"),
        nc_policy("X1", "ins1", status="Active"),
    ])
    res = run(supa, nc)
    assert res.policies_created == 1
    assert supa.tables[cbs.POLICIES_TABLE][0]["status"] == "Active"


# --- schema adaptivity -------------------------------------------------------
def test_writes_filtered_to_existing_columns():
    # Seed an existing row whose column set omits `carrier` — discovery should
    # detect the live schema and drop `carrier` from a new-policy insert.
    supa = FakeSupabase({cbs.POLICIES_TABLE: [{
        "id": "pol-0", "policy_number": "SEED", "status": "Active",
        "expiration_date": "2027-01-01", "synced_at": "2026-06-10T00:00:00Z",
        "policy_guid": "pg-SEED", "nowcerts_insured_guid": "ins1",
        "annualized_premium": 1.0, "premium_amount": 1.0, "active": True,
        "renewed_policy": None, "client_id": None, "effective_date": "2026-01-01",
    }]})
    nc = FakeNowCerts(policies=[nc_policy("NEW1", "ins1", carrier="Ghost Carrier")])
    run(supa, nc)
    new_row = next(p for p in supa.tables[cbs.POLICIES_TABLE] if p["policy_number"] == "NEW1")
    assert "carrier" not in new_row  # unknown column never written


# --- dry run -----------------------------------------------------------------
def test_dry_run_makes_no_writes():
    supa = FakeSupabase()
    nc = FakeNowCerts(
        insureds=[nc_insured("ins1", "Acme LLC")],
        policies=[nc_policy("P1", "ins1")],
    )
    res = run(supa, nc, dry_run=True)
    assert res.clients_created == 1 and res.policies_created == 1  # counted
    assert supa.tables.get("canonical_clients", []) == []          # but not written
    assert supa.tables.get(cbs.POLICIES_TABLE, []) == []


def test_limit_caps_records():
    supa = FakeSupabase()
    nc = FakeNowCerts(
        insureds=[nc_insured(f"ins{i}", f"Co {i}") for i in range(5)],
        policies=[nc_policy(f"P{i}", f"ins{i}") for i in range(5)],
    )
    res = run(supa, nc, limit=2)
    assert res.insureds_fetched == 2 and res.policies_fetched == 2
