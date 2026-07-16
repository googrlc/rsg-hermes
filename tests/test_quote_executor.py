"""Tests for the quote write-back executor (opportunity → NowCerts quote)."""
from __future__ import annotations

import pytest

from hermes.quotes import executor as qx


class FakeSupa:
    def __init__(self, queue=None, opps=None):
        self.tables = {"outbound_sync_queue": queue or [], "opportunities": opps or []}
        self._n = 0

    def insert(self, table, payload):
        self._n += 1
        row = {"id": f"{table[:2]}-{self._n}", **payload}
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def select(self, table, *, columns="*", params=None, limit=100):
        rows = self.tables.get(table, [])
        for k, v in (params or {}).items():
            if k == "order":
                continue
            if isinstance(v, str) and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
        return [dict(r) for r in rows][:limit]

    def update_where(self, table, payload, *, filters):
        matched = []
        for r in self.tables.get(table, []):
            if all(str(r.get(c)) == val.split("eq.", 1)[1] for c, val in filters.items()):
                r.update(payload)
                matched.append(dict(r))
        return matched

    def update(self, table, rid, payload):
        for r in self.tables.get(table, []):
            if str(r.get("id")) == str(rid):
                r.update(payload)
                return dict(r)
        return {}


class FakeNowCerts:
    def __init__(self, resp=None, raise_exc=None):
        self.resp = resp if resp is not None else {"data": {"databaseId": "qg-1", "number": "Q-1001"}}
        self.raise_exc = raise_exc
        self.calls = []

    def insert_policy(self, payload):
        self.calls.append(payload)
        if self.raise_exc:
            raise self.raise_exc
        return self.resp


def opp(**o):
    base = {"id": "opp-1", "insured_id": "ins-1", "insured_name": "Acme LLC",
            "line_of_business": "General Liability", "carrier": "Acme Mutual",
            "premium_estimate": 2500, "stage": "Quoted"}
    base.update(o)
    return base


def _queued(policy):
    return {"id": "q1", "object_type": "quote", "destination_system": "nowcerts",
            "status": "queued", "payload": {"opportunity_id": "opp-1", "policy": policy}}


def test_map_opportunity_to_quote():
    p = qx.map_opportunity_to_quote(opp())
    assert p["IsQuote"] is True
    assert p["InsuredDatabaseId"] == "ins-1"
    assert p["LineOfBusinessName"] == "General Liability"
    assert p["CarrierName"] == "Acme Mutual"
    assert p["Premium"] == 2500.0
    assert p["InsuredName"] == "Acme LLC"


def test_stage_requires_linked_insured():
    with pytest.raises(ValueError):
        qx.stage_quote_job(FakeSupa(), opportunity=opp(insured_id=None), approved_by="lamar@x")


def test_stage_enqueues_approved_quote():
    supa = FakeSupa()
    qx.stage_quote_job(supa, opportunity=opp(), approved_by="lamar@risksolutionsgroup.net")
    row = supa.tables["outbound_sync_queue"][0]
    assert row["object_type"] == "quote" and row["destination_system"] == "nowcerts"
    assert row["status"] == "queued" and row["approved_by"] == "lamar@risksolutionsgroup.net"
    assert row["payload"]["policy"]["IsQuote"] is True
    assert row["payload"]["action"] == "insert_quote"


def test_dry_run_previews_without_writing():
    supa = FakeSupa(queue=[_queued({"IsQuote": True})])
    res = qx.run_quote_executor(supa=supa, nowcerts=FakeNowCerts(), dry_run=True)
    assert res["claimed"] == 0 and len(res["previews"]) == 1
    assert supa.tables["outbound_sync_queue"][0]["status"] == "queued"  # untouched


def test_live_writes_quote_and_stamps_opportunity():
    supa = FakeSupa(queue=[_queued({"IsQuote": True, "InsuredDatabaseId": "ins-1"})],
                    opps=[{"id": "opp-1", "client_identifier": "acme", "line_of_business": "GL"}])
    nc = FakeNowCerts()
    res = qx.run_quote_executor(supa=supa, nowcerts=nc, limit=1)
    assert res["claimed"] == 1 and res["completed"] == 1 and res["failed"] == 0
    assert nc.calls and nc.calls[0]["IsQuote"] is True
    assert supa.tables["outbound_sync_queue"][0]["status"] == "completed"
    o = supa.tables["opportunities"][0]
    assert o["quote_number"] == "Q-1001" and o["nowcerts_quote_guid"] == "qg-1"


def test_no_id_returned_marks_failed():
    supa = FakeSupa(queue=[_queued({"IsQuote": True})])
    nc = FakeNowCerts(resp={})            # NowCerts returned nothing usable
    res = qx.run_quote_executor(supa=supa, nowcerts=nc, limit=1)
    assert res["failed"] == 1
    assert supa.tables["outbound_sync_queue"][0]["status"] == "failed"


def test_exception_marks_failed_with_error():
    supa = FakeSupa(queue=[_queued({"IsQuote": True})])
    nc = FakeNowCerts(raise_exc=RuntimeError("boom"))
    res = qx.run_quote_executor(supa=supa, nowcerts=nc, limit=1)
    assert res["failed"] == 1
    assert "boom" in supa.tables["outbound_sync_queue"][0]["last_error"]
