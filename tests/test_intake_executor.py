"""Intake drain executor — CRM intent -> opportunities, AMS intent -> NowCerts insured."""
from __future__ import annotations

from hermes.command_center import intake_executor as X


class FakeSupa:
    def __init__(self, queue):
        self.tables = {"outbound_sync_queue": queue, "opportunities": []}

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
            elif isinstance(v, str) and v.startswith("in."):
                vals = v[4:-1].split(",")
                rows = [r for r in rows if str(r.get(k)) in vals]
        return [dict(r) for r in rows][:limit]

    def insert(self, table, payload):
        row = {"id": f"{table[:3]}-{len(self.tables.get(table, [])) + 1}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def update_where(self, table, payload, *, filters):
        out = []
        for r in self.tables.get(table, []):
            if all(str(r.get(c)) == val.split("eq.", 1)[1] for c, val in filters.items()):
                r.update(payload)
                out.append(dict(r))
        return out

    def update(self, table, rid, payload):
        for r in self.tables.get(table, []):
            if str(r.get("id")) == str(rid):
                r.update(payload)
                return dict(r)
        return {}


class FakeNowCerts:
    def __init__(self):
        self.calls = []

    def create_insured(self, payload):
        self.calls.append(payload)
        return {"data": {"database_id": "nc-ins-1"}}


def _crm_job(**o):
    base = {"id": "q-crm", "object_type": "intake_crm", "status": "queued",
            "payload": {"kind": "opportunity", "opportunity": {
                "insured_name": "Acme LLC", "line_of_business": "General Liability",
                "opportunity_type": "New Business", "stage": "new",
                "premium_estimate": 12000, "carrier": "Travelers"}}}
    base.update(o)
    return base


def _ams_job(**o):
    base = {"id": "q-ams", "object_type": "intake_ams", "status": "queued",
            "payload": {"kind": "insured_bundle", "ams": {"insured": {
                "name": "Acme LLC", "fein": "12-3456789", "city": "Atlanta", "state": "GA"}}}}
    base.update(o)
    return base


# --- mapping ---
def test_map_opportunity_row():
    row = X.map_opportunity_row({"insured_name": "Acme LLC", "line_of_business": "GL", "premium_estimate": 100})
    assert row["client_identifier"] == "Acme LLC" and row["sync_source"] == "intake" and row["source"] == "intake"
    assert row["opportunity_type"] == "New Business" and row["stage"] == "new"


def test_map_insured_payload_pascalcase_and_drops_empty():
    body = X.map_insured_payload({"name": "Acme LLC", "fein": "12-3456789", "city": "", "email": None})
    assert body == {"CommercialName": "Acme LLC", "FEIN": "12-3456789"}


# --- executor ---
def test_dry_run_writes_nothing():
    supa = FakeSupa([_crm_job(), _ams_job()])
    res = X.run_intake_executor(supa=supa, nowcerts=FakeNowCerts(), limit=5, dry_run=True)
    assert res["claimed"] == 0 and len(res["previews"]) == 2
    assert supa.tables["outbound_sync_queue"][0]["status"] == "queued"
    assert supa.tables["opportunities"] == []


def test_crm_intent_creates_opportunity():
    supa = FakeSupa([_crm_job()])
    res = X.run_intake_executor(supa=supa, nowcerts=FakeNowCerts(), limit=5)
    assert res["crm"] == 1 and res["claimed"] == 1
    opp = supa.tables["opportunities"][0]
    assert opp["insured_name"] == "Acme LLC" and opp["line_of_business"] == "General Liability"
    assert supa.tables["outbound_sync_queue"][0]["status"] == "completed"


def test_ams_intent_creates_insured():
    supa = FakeSupa([_ams_job()])
    nc = FakeNowCerts()
    res = X.run_intake_executor(supa=supa, nowcerts=nc, limit=5)
    assert res["ams"] == 1
    assert nc.calls and nc.calls[0]["CommercialName"] == "Acme LLC" and nc.calls[0]["FEIN"] == "12-3456789"
    assert supa.tables["outbound_sync_queue"][0]["status"] == "completed"


def test_ams_missing_insured_fails_gracefully():
    supa = FakeSupa([_ams_job(payload={"kind": "insured_bundle", "ams": {}})])
    res = X.run_intake_executor(supa=supa, nowcerts=FakeNowCerts(), limit=5)
    assert res["failed"] == 1
    assert supa.tables["outbound_sync_queue"][0]["status"] == "failed"
