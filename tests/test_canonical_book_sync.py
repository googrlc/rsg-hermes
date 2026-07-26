"""Tests for the NowCerts → Supabase canonical book sync.

Keyed on the LIVE natural keys (canonical_clients.nowcerts_insured_guid,
canonical_policies.policy_guid — the CSV-loaded tables have no surrogate id).
Covers: client upsert, policy insert/update, renewed_policy lineage preservation,
missing-guid skip, schema-adaptive column filtering, and dry-run no-op.
"""
from __future__ import annotations

from typing import Any

from hermes.sync import canonical_book_sync as cbs


# --- fakes -------------------------------------------------------------------
class FakeSupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = tables or {}

    def select(self, table, *, columns="*", params=None, limit=100):
        return [dict(r) for r in self.tables.get(table, [])][:limit]

    def insert(self, table, payload):
        self.tables.setdefault(table, []).append(dict(payload))
        return dict(payload)

    def update_where(self, table, payload, *, filters):
        matched = []
        for r in self.tables.get(table, []):
            if all(str(r.get(col)) == val.split("eq.", 1)[1] for col, val in filters.items()):
                r.update(payload)
                matched.append(dict(r))
        return matched


class FakeNowCerts:
    def __init__(self, insureds=None, policies=None) -> None:
        self._insureds = insureds or []
        self._policies = policies or []

    def fetch_insureds(self, *, since=None, page_size=100, max_pages=1000):
        return list(self._insureds)

    def fetch_policies(self, *, since=None, page_size=100, max_pages=1000):
        return list(self._policies)


# --- builders ----------------------------------------------------------------
def nc_insured(guid, name="Acme LLC", *, itype="Commercial"):
    return {"id": guid, "commercialName": name, "insuredType": itype,
            "firstName": "", "lastName": "", "eMail": "a@b.com", "city": "Atlanta"}


def nc_policy(number, insured_guid="ins1", *, guid=None, status="Active",
              eff="2026-01-01", exp="2027-01-01", premium=1000.0,
              carrier="Acme Mutual", lob="General Liability"):
    return {
        "number": number, "databaseId": guid or f"pg-{number}",
        "insuredDatabaseId": insured_guid, "status": status,
        "effectiveDate": eff, "expirationDate": exp, "totalPremium": premium,
        "carrierName": carrier, "lineOfBusinesses": [{"lineOfBusinessName": lob}],
    }


def _live_policy_row(**over):
    """A realistic canonical_policies row — every live column present (nulls allowed),
    mirroring what PostgREST returns and what column discovery introspects."""
    row = {
        "policy_guid": "pg-P1", "nowcerts_insured_guid": "ins1", "policy_number": "P1",
        "lines_of_business": "General Liability", "business_type": None, "carrier": "Old Carrier",
        "status": "Active", "active": True, "effective_date": "2025-09-01",
        "expiration_date": "2026-09-01", "current_term_amount": 1000.0,
        "premium_amount": 1000.0, "annualized_premium": 1000.0,
        "renewed_policy": None, "state": "GA", "source_file": "csv", "created_at": "2026-06-10",
    }
    row.update(over)
    return row


def _live_client_row(**over):
    row = {
        "nowcerts_insured_guid": "ins1", "insured_name": "Old Name",
        "insured_name_normalized": "old name", "first_name": None, "last_name": None,
        "client_type": "Commercial", "business_type": None, "phone": None,
        "cell_phone": None, "email": None, "address_line1": None, "city": None,
        "state": None, "zip": None, "created_at": "2026-06-10",
        "updated_at": "2026-06-10",
    }
    row.update(over)
    return row


def run(supa, nc, **kw):
    return cbs.run_canonical_book_sync(nc, supa, **kw)


# --- clients -----------------------------------------------------------------
def test_new_insured_creates_client():
    supa = FakeSupabase({cbs.CLIENTS_TABLE: [_live_client_row(nowcerts_insured_guid="seed")]})
    nc = FakeNowCerts(insureds=[nc_insured("ins1", "Acme LLC")])
    res = run(supa, nc)
    assert res.clients_created == 1
    client = next(c for c in supa.tables[cbs.CLIENTS_TABLE] if c[cbs.CLIENT_KEY] == "ins1")
    assert client["insured_name"] == "Acme LLC"
    assert client["insured_name_normalized"] == "acme llc"


def test_existing_client_updates_not_duplicates():
    supa = FakeSupabase({cbs.CLIENTS_TABLE: [_live_client_row(nowcerts_insured_guid="ins1", insured_name="Old Name")]})
    nc = FakeNowCerts(insureds=[nc_insured("ins1", "New Name")])
    res = run(supa, nc)
    assert res.clients_created == 0 and res.clients_updated == 1
    rows = [c for c in supa.tables[cbs.CLIENTS_TABLE] if c[cbs.CLIENT_KEY] == "ins1"]
    assert len(rows) == 1 and rows[0]["insured_name"] == "New Name"


def test_insured_without_guid_or_name_skipped():
    supa = FakeSupabase()
    nc = FakeNowCerts(insureds=[{"id": "", "commercialName": ""}, {"id": "x", "commercialName": ""}])
    res = run(supa, nc)
    assert res.clients_created == 0
    assert supa.tables.get(cbs.CLIENTS_TABLE, []) == []


# --- policies ----------------------------------------------------------------
def test_new_policy_inserted_by_guid():
    supa = FakeSupabase()
    nc = FakeNowCerts(policies=[nc_policy("P1", "ins1", premium=2500.0)])
    res = run(supa, nc)
    assert res.policies_created == 1 and res.policies_updated == 0
    pol = supa.tables[cbs.POLICIES_TABLE][0]
    assert pol["policy_guid"] == "pg-P1"
    assert pol["policy_number"] == "P1"
    assert pol["nowcerts_insured_guid"] == "ins1"
    assert pol["lines_of_business"] == "General Liability"
    assert pol["annualized_premium"] == 2500.0 and pol["premium_amount"] == 2500.0
    assert pol["active"] is True


def test_policy_without_guid_skipped():
    supa = FakeSupabase()
    nc = FakeNowCerts(policies=[{"number": "NOGUID", "insuredDatabaseId": "ins1", "status": "Active"}])
    res = run(supa, nc)
    assert res.policies_skipped_no_guid == 1 and res.policies_created == 0


def test_existing_policy_refreshes_volatile_and_preserves_lineage():
    supa = FakeSupabase({cbs.POLICIES_TABLE: [
        _live_policy_row(policy_guid="pg-P1", renewed_policy="P0", status="Active")
    ]})
    nc = FakeNowCerts(policies=[nc_policy("P1", "ins9", guid="pg-P1", status="Renewing",
                                          exp="2027-09-01", premium=3300.0)])
    res = run(supa, nc)
    assert res.policies_updated == 1 and res.policies_created == 0
    pol = supa.tables[cbs.POLICIES_TABLE][0]
    assert pol["renewed_policy"] == "P0"          # lineage preserved (never sent on update)
    assert pol["status"] == "Renewing"            # volatile refreshed
    assert pol["expiration_date"] == "2027-09-01"
    assert pol["premium_amount"] == 3300.0


# --- schema adaptivity -------------------------------------------------------
def test_writes_filtered_to_existing_columns():
    # Seed a live row whose column set omits `carrier`; a new-policy insert must
    # drop `carrier` rather than error on an unknown column.
    seed = _live_policy_row(policy_guid="pg-SEED", policy_number="SEED")
    seed.pop("carrier")
    supa = FakeSupabase({cbs.POLICIES_TABLE: [seed]})
    nc = FakeNowCerts(policies=[nc_policy("NEW1", "ins1", carrier="Ghost Carrier")])
    run(supa, nc)
    new_row = next(p for p in supa.tables[cbs.POLICIES_TABLE] if p["policy_number"] == "NEW1")
    assert "carrier" not in new_row


# --- dry run / limit ---------------------------------------------------------
def test_dry_run_makes_no_writes():
    supa = FakeSupabase()
    nc = FakeNowCerts(insureds=[nc_insured("ins1")], policies=[nc_policy("P1", "ins1")])
    res = run(supa, nc, dry_run=True)
    assert res.clients_created == 1 and res.policies_created == 1  # counted
    assert supa.tables.get(cbs.CLIENTS_TABLE, []) == []            # not written
    assert supa.tables.get(cbs.POLICIES_TABLE, []) == []


def test_limit_caps_records():
    supa = FakeSupabase()
    nc = FakeNowCerts(
        insureds=[nc_insured(f"ins{i}", f"Co {i}") for i in range(5)],
        policies=[nc_policy(f"P{i}", f"ins{i}") for i in range(5)],
    )
    res = run(supa, nc, limit=2)
    assert res.insureds_fetched == 2 and res.policies_fetched == 2



# --- audit trail (#232) ------------------------------------------------------
def _audit_rows(supa):
    return supa.tables.get(cbs.AUDIT_TABLE, [])


def test_audit_logs_create_and_update_for_clients():
    supa = FakeSupabase({cbs.CLIENTS_TABLE: [_live_client_row(nowcerts_insured_guid="ins1")]})
    nc = FakeNowCerts(insureds=[nc_insured("ins1", "Updated"), nc_insured("ins2", "Brand New")])
    run(supa, nc)
    rows = _audit_rows(supa)
    actions = {(r["source_object_id"], r["action"]) for r in rows
               if r["object_type"] == cbs.AUDIT_OBJECT_CLIENT}
    assert ("ins1", "update") in actions
    assert ("ins2", "create") in actions
    # Every audit row is a success and carries the object type.
    assert all(r["status"] == "success" for r in rows)


def test_audit_logs_create_and_update_for_policies():
    supa = FakeSupabase({cbs.POLICIES_TABLE: [_live_policy_row(policy_guid="pg-P1")]})
    nc = FakeNowCerts(policies=[nc_policy("P1", "ins1", guid="pg-P1"),
                                nc_policy("P2", "ins1", guid="pg-P2")])
    run(supa, nc)
    rows = _audit_rows(supa)
    actions = {(r["source_object_id"], r["action"]) for r in rows
               if r["object_type"] == cbs.AUDIT_OBJECT_POLICY}
    assert ("pg-P1", "update") in actions
    assert ("pg-P2", "create") in actions


def test_audit_logs_skip_for_missing_guid():
    supa = FakeSupabase()
    nc = FakeNowCerts(policies=[{"number": "NOGUID", "insuredDatabaseId": "ins1", "status": "Active"}])
    run(supa, nc)
    skips = [r for r in _audit_rows(supa) if r["action"] == "skip"]
    assert skips and skips[0]["object_type"] == cbs.AUDIT_OBJECT_POLICY
    assert "guid" in skips[0]["message"]


def test_audit_logs_error_when_a_write_fails():
    class BoomSupa(FakeSupabase):
        def insert(self, table, payload):
            if table == cbs.POLICIES_TABLE:
                raise RuntimeError("supabase 500")
            return super().insert(table, payload)
    supa = BoomSupa()
    nc = FakeNowCerts(policies=[nc_policy("P1", "ins1", guid="pg-P1")])
    run(supa, nc)
    errs = [r for r in _audit_rows(supa) if r["action"] == "error"]
    assert errs and errs[0]["status"] == "failed"
    assert "supabase 500" in errs[0]["message"]
    assert errs[0]["object_type"] == cbs.AUDIT_OBJECT_POLICY


def test_dry_run_writes_no_audit_rows():
    supa = FakeSupabase()
    nc = FakeNowCerts(insureds=[nc_insured("ins1")], policies=[nc_policy("P1", "ins1")])
    run(supa, nc, dry_run=True)
    assert _audit_rows(supa) == []
