"""Tests for the opportunity terminal writeback (CRM → NowCerts)."""
from __future__ import annotations

from hermes.sync import opportunity_writeback as wb
from hermes.sync.opportunity_writeback import _writeback_payload, terminal_stage_for


class FakeSupa:
    def __init__(self, queue=None):
        self.rows = list(queue or [])
        self.inserts = []

    def insert(self, table, payload):
        self.inserts.append((table, payload))
        r = {"id": f"q{len(self.inserts)}", **payload}
        self.rows.append(r)
        return r

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.rows
        for k, v in (params or {}).items():
            if k == "order" or not isinstance(v, str):
                continue
            if v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
            elif v == "not.is.null":
                rows = [r for r in rows if r.get(k) is not None]
        return [dict(r) for r in rows][:limit]

    def update(self, table, rid, payload):
        for r in self.rows:
            if str(r.get("id")) == str(rid):
                r.update(payload)
                return dict(r)
        return {}

    def update_where(self, table, payload, *, filters):
        out = []
        for r in self.rows:
            if all(str(r.get(k)) == v[3:] for k, v in filters.items() if isinstance(v, str) and v.startswith("eq.")):
                r.update(payload)
                out.append(r)
        return out


class FakeNC:
    def __init__(self, opps):
        self._o = {str(o.get("id")): o for o in opps}
        self.inserted = []

    def find_opportunity(self, oid):
        return self._o.get(str(oid))

    def insert_opportunity(self, payload):
        self.inserted.append(payload)
        return {"databaseId": payload.get("databaseId")}


def test_terminal_stage_for():
    assert terminal_stage_for("won") == "Bound / Won"
    assert terminal_stage_for("Bound / Won") == "Bound / Won"
    assert terminal_stage_for("lost") == "Lost"
    assert terminal_stage_for("Quotes Received") is None


def test_stage_writeback_queues_only_terminal_mirrored():
    supa = FakeSupa()
    assert wb.stage_writeback(supa, {"id": "o1", "nowcerts_opportunity_id": "NCO-1", "status": "open"}, approved_by="u") is None
    assert wb.stage_writeback(supa, {"id": "o2", "status": "won"}, approved_by="u") is None   # not mirrored
    job = wb.stage_writeback(supa, {"id": "o3", "nowcerts_opportunity_id": "NCO-3", "status": "won"}, approved_by="u")
    assert job and job["payload"]["target_stage"] == "Bound / Won" and job["approved_by"] == "u"
    assert job["object_type"] == "opportunity_writeback"


def test_writeback_payload_roundtrips_and_coerces():
    fresh = {"id": "NCO-1", "lineOfBusinessName": "General Liability", "neededBy": "2026-07-24",
             "winProbability": "VeryGood", "agencyCommission": 10, "assignedTo": "g@x",
             "opportunityStageName": "Quotes Received", "dispositionDatabaseId": "D1"}
    p = _writeback_payload(fresh, "Bound / Won")
    assert p["databaseId"] == "NCO-1" and p["opportunityStageName"] == "Bound / Won"
    assert p["assignedTo"] == ["g@x"]           # coerced to array
    assert p["dispositionDatabaseId"] == "D1"   # existing disposition round-tripped
    assert p["lineOfBusinessName"] == "General Liability"


def test_writeback_payload_fills_required_when_missing():
    p = _writeback_payload({"id": "X"}, "Lost")
    assert p["winProbability"] == "Good" and p["agencyCommission"] == 0 and p["assignedTo"] == []


def _job(target):
    return {"id": "q1", "object_type": "opportunity_writeback", "destination_system": "nowcerts",
            "status": "queued", "approved_by": "u", "approved_at": "t",
            "payload": {"nowcerts_opportunity_id": "NCO-1", "target_stage": target}}


def test_executor_writes_stage_and_completes():
    supa = FakeSupa([_job("Bound / Won")])
    nc = FakeNC([{"id": "NCO-1", "lineOfBusinessName": "GL", "neededBy": "2026-07-24",
                  "winProbability": "Good", "agencyCommission": 0, "assignedTo": ["g"],
                  "opportunityStageName": "Quotes Received"}])
    res = wb.run_opportunity_writeback_executor(supa=supa, nowcerts=nc, limit=5)
    assert res["completed"] == 1 and res["failed"] == 0
    assert nc.inserted and nc.inserted[0]["opportunityStageName"] == "Bound / Won"
    assert next(r for r in supa.rows if r["id"] == "q1")["status"] == "completed"


def test_executor_fails_when_opp_missing():
    supa = FakeSupa([_job("Lost")])
    nc = FakeNC([])   # opportunity not found in AMS
    res = wb.run_opportunity_writeback_executor(supa=supa, nowcerts=nc, limit=5)
    assert res["failed"] == 1 and not nc.inserted


def test_executor_dry_run_no_write():
    supa = FakeSupa([_job("Lost")])
    nc = FakeNC([])
    res = wb.run_opportunity_writeback_executor(supa=supa, nowcerts=nc, dry_run=True, limit=5)
    assert res["previews"] and not nc.inserted and res["claimed"] == 0
