"""Tests for the case/task write-back executor (agency_crm → NowCerts task)."""
from __future__ import annotations

import pytest

from hermes.casework import executor as cw


class FakeSupa:
    def __init__(self, queue=None, extra=None):
        self.tables = {"outbound_sync_queue": queue or [], **(extra or {})}
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
            elif isinstance(v, str) and v.startswith("in."):
                vals = v[4:-1].split(",")
                rows = [r for r in rows if str(r.get(k)) in vals]
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
    def __init__(self, resp=None):
        self.resp = resp if resp is not None else {"data": {"database_id": "nt-1"}}
        self.calls = []

    def insert_task(self, payload):
        self.calls.append(payload)
        return self.resp


def case(**o):
    base = {"id": "case-1", "case_number": "SER-1", "title": "COI request", "description": "Need COI",
            "priority": "high", "insured_database_id": "ins-1", "policy_number": "P1", "case_type": "service"}
    base.update(o)
    return base


def task(**o):
    base = {"id": "task-1", "case_id": "case-1", "title": "Call client", "priority": "medium", "status": "not_started"}
    base.update(o)
    return base


def _q(object_type, target_table, target_id, task_payload):
    return {"id": "q1", "object_type": object_type, "destination_system": "nowcerts", "status": "queued",
            "payload": {"action": "insert_task", "target_table": target_table, "target_id": target_id, "task": task_payload}}


# --- mapping ---
def test_map_case_to_task():
    t = cw.map_case_to_task(case())
    assert t["title"] == "COI request" and t["priority"] == "High" and t["status"] == "Open"
    assert t["insured_database_id"] == "ins-1" and t["policy_number"] == "P1" and t["category_name"] == "Service"


def test_map_task_to_task_completed():
    t = cw.map_task_to_task(task(status="completed"), insured_database_id="ins-9")
    assert t["status"] == "Completed" and t["insured_database_id"] == "ins-9"
    assert t["priority"] == "Normal" and t["category_name"] == "Task"


# --- staging guards ---
def test_stage_case_requires_insured():
    with pytest.raises(ValueError):
        cw.stage_case_job(FakeSupa(), case=case(insured_database_id=None), approved_by="x")


def test_stage_task_requires_insured():
    with pytest.raises(ValueError):
        cw.stage_task_job(FakeSupa(), task=task(), insured_database_id=None, approved_by="x")


def test_stage_case_enqueues():
    supa = FakeSupa()
    cw.stage_case_job(supa, case=case(), approved_by="lamar@x")
    row = supa.tables["outbound_sync_queue"][0]
    assert row["object_type"] == "case" and row["approved_by"] == "lamar@x"
    assert row["payload"]["target_table"] == "agency_crm_cases"
    assert row["payload"]["task"]["title"] == "COI request"


# --- executor ---
def test_dry_run_no_write():
    supa = FakeSupa(queue=[_q("case", "agency_crm_cases", "case-1", {"title": "X", "insured_database_id": "ins-1"})])
    res = cw.run_casework_executor(supa=supa, nowcerts=FakeNowCerts(), dry_run=True)
    assert res["claimed"] == 0 and len(res["previews"]) == 1
    assert supa.tables["outbound_sync_queue"][0]["status"] == "queued"


def test_live_case_writes_and_stamps():
    supa = FakeSupa(queue=[_q("case", "agency_crm_cases", "case-1", {"title": "X", "insured_database_id": "ins-1"})],
                    extra={"agency_crm_cases": [{"id": "case-1"}]})
    nc = FakeNowCerts()
    res = cw.run_casework_executor(supa=supa, nowcerts=nc, limit=1)
    assert res["completed"] == 1 and nc.calls
    assert supa.tables["outbound_sync_queue"][0]["status"] == "completed"
    row = supa.tables["agency_crm_cases"][0]
    assert row["nowcerts_task_id"] == "nt-1"
    assert "sync_status" not in row               # cases have no sync_status column


def test_live_task_stamps_sync_status():
    supa = FakeSupa(queue=[_q("task", "agency_crm_tasks", "task-1", {"title": "X", "insured_database_id": "ins-1"})],
                    extra={"agency_crm_tasks": [{"id": "task-1"}]})
    res = cw.run_casework_executor(supa=supa, nowcerts=FakeNowCerts(), limit=1)
    assert res["completed"] == 1
    row = supa.tables["agency_crm_tasks"][0]
    assert row["nowcerts_task_id"] == "nt-1" and row["sync_status"] == "synced"


def test_no_id_fails():
    supa = FakeSupa(queue=[_q("case", "agency_crm_cases", "case-1", {"title": "X"})])
    res = cw.run_casework_executor(supa=supa, nowcerts=FakeNowCerts(resp={}), limit=1)
    assert res["failed"] == 1
    assert supa.tables["outbound_sync_queue"][0]["status"] == "failed"


# --- retriable on command (requeue) ---
def _failed_q(object_type="task", status="failed", **o):
    row = {"id": "q-fail", "object_type": object_type, "destination_system": "nowcerts",
           "status": status, "attempt_count": 1, "last_error": "boom", "scheduled_for": "2999-01-01T00:00:00Z",
           "payload": {"action": "insert_task", "target_table": "agency_crm_tasks",
                       "target_id": "task-1", "task": {"title": "X", "insured_database_id": "ins-1"}}}
    row.update(o)
    return row


def test_requeue_reopens_failed_job():
    supa = FakeSupa(queue=[_failed_q(status="failed")])
    job = cw.requeue_job(supa, queue_id="q-fail")
    assert job["status"] == "queued"
    assert job["attempt_count"] == 2          # bumped
    assert job["last_error"] is None          # cleared
    assert job["scheduled_for"] is None       # backoff cleared


def test_requeue_reopens_dead_job():
    supa = FakeSupa(queue=[_failed_q(status="dead")])
    job = cw.requeue_job(supa, queue_id="q-fail")
    assert job["status"] == "queued"


def test_requeue_rejects_completed():
    supa = FakeSupa(queue=[_failed_q(status="completed")])
    with pytest.raises(ValueError):
        cw.requeue_job(supa, queue_id="q-fail")


def test_requeue_rejects_non_casework_object():
    supa = FakeSupa(queue=[_failed_q(object_type="renewal")])
    with pytest.raises(ValueError):
        cw.requeue_job(supa, queue_id="q-fail")


def test_requeue_missing_row():
    with pytest.raises(ValueError):
        cw.requeue_job(FakeSupa(), queue_id="nope")


def test_requeue_then_executor_completes():
    supa = FakeSupa(queue=[_failed_q(status="failed")], extra={"agency_crm_tasks": [{"id": "task-1"}]})
    cw.requeue_job(supa, queue_id="q-fail")
    res = cw.run_casework_executor(supa=supa, nowcerts=FakeNowCerts(), limit=5)
    assert res["completed"] == 1
    assert supa.tables["outbound_sync_queue"][0]["status"] == "completed"
