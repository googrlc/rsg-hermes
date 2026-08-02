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
            "priority": "high", "insured_database_id": "ins-1", "policy_number": "P1", "case_type": "service",
            # NowCerts requires a due date on every task; see MissingDueDate.
            "due_at": "2026-08-15"}
    base.update(o)
    return base


def task(**o):
    base = {"id": "task-1", "case_id": "case-1", "title": "Call client", "priority": "medium",
            "status": "not_started", "due_at": "2026-08-15"}
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
    assert t["due_date"], "NowCerts requires due_date on InsertTask"


def test_map_task_to_task_completed():
    t = cw.map_task_to_task(task(status="completed"), insured_database_id="ins-9")
    assert t["status"] == "Completed" and t["insured_database_id"] == "ins-9"
    assert t["priority"] == "Normal" and t["category_name"] == "Task"
    assert t["due_date"], "NowCerts requires due_date on InsertTask"


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


# --- the due date NowCerts requires -------------------------------------------
# Every InsertTask needs one, and we were sending none. That is consistent with
# what the live data shows: 18 tasks, and not one carries a nowcerts_task_id.

def test_a_task_without_a_due_date_is_refused_at_stage_time():
    """Refused where someone can still fix it, not as a queue row found later."""
    t = task()
    t.pop("due_at")
    with pytest.raises(cw.MissingDueDate) as exc:
        cw.stage_task_job(FakeSupa(), task=t, insured_database_id="ins-1", approved_by="x")
    assert "due date" in str(exc.value).lower()
    assert "Call client" in str(exc.value), "the message must name the task"


def test_a_case_without_a_due_date_is_refused_at_stage_time():
    c = case()
    c.pop("due_at")
    with pytest.raises(cw.MissingDueDate):
        cw.stage_case_job(FakeSupa(), case=c, approved_by="x")


def test_nothing_is_queued_when_the_due_date_is_missing():
    """A refusal must not leave a half-staged job behind."""
    supa = FakeSupa()
    t = task()
    t.pop("due_at")
    with pytest.raises(cw.MissingDueDate):
        cw.stage_task_job(supa, task=t, insured_database_id="ins-1", approved_by="x")
    assert supa.tables.get("outbound_sync_queue", []) == []


def test_the_due_date_is_normalised_not_passed_through_raw():
    """due_at can arrive as a full timestamp; NowCerts wants the agency's date."""
    t = cw.map_task_to_task(task(due_at="2026-08-15T21:30:00Z"), insured_database_id="ins-1")
    assert t["due_date"].startswith("2026-08-1"), t["due_date"]


def test_a_due_date_is_never_invented():
    """Defaulting would put a date on a real client task that somebody works to."""
    import inspect
    src = inspect.getsource(cw._due_date)
    assert "agency_today" not in src and "due_in_days" not in src, (
        "_due_date must not manufacture a date when the row has none"
    )


# --- who a pushed task is assigned to -----------------------------------------
# insert_task_tool takes assigned_to as NowCerts agent UUIDs. We store the id on
# agency_crm_users and read it; we never resolve a person by name at write time.
# A near-miss on a name puts a client's task on the wrong person's list, where it
# looks handled. Unassigned is visible; misassigned is not.

class _UsersSupa:
    def __init__(self, rows): self.rows = rows; self.queries = []
    def select(self, table, *, columns="*", params=None, limit=100):
        self.queries.append((table, dict(params or {})))
        if table != "agency_crm_users":
            return []
        want = (params or {}).get("email", "").replace("eq.", "")
        return [r for r in self.rows if r.get("email") == want]


def test_a_known_user_resolves_to_their_stored_agent_id():
    supa = _UsersSupa([{"email": "g@rsg.net", "nowcerts_agent_id": "agent-uuid-1"}])
    assert cw.nowcerts_agent_id(supa, "g@rsg.net") == "agent-uuid-1"


def test_a_user_without_an_agent_id_resolves_to_none_not_a_guess():
    supa = _UsersSupa([{"email": "g@rsg.net", "nowcerts_agent_id": None}])
    assert cw.nowcerts_agent_id(supa, "g@rsg.net") is None


def test_an_unknown_user_resolves_to_none():
    assert cw.nowcerts_agent_id(_UsersSupa([]), "nobody@rsg.net") is None


def test_no_email_does_not_hit_the_database():
    supa = _UsersSupa([])
    assert cw.nowcerts_agent_id(supa, None) is None
    assert supa.queries == []


def test_a_lookup_failure_leaves_the_task_unassigned_rather_than_failing_the_push():
    class Boom:
        def select(self, *a, **k): raise RuntimeError("postgrest down")
    assert cw.nowcerts_agent_id(Boom(), "g@rsg.net") is None


def test_assigned_to_is_attached_only_when_the_agent_is_known():
    base = {"title": "Call client"}
    assert cw.with_assignee(base, "agent-uuid-1") == {"title": "Call client",
                                                      "assigned_to": ["agent-uuid-1"]}
    # Unknown => the key is absent entirely, not present-and-empty. An empty list
    # could read as "explicitly assigned to nobody" and clear an existing owner.
    assert cw.with_assignee(base, None) == {"title": "Call client"}
    assert "assigned_to" not in cw.with_assignee(base, None)


def test_with_assignee_does_not_mutate_the_payload_it_is_given():
    base = {"title": "Call client"}
    cw.with_assignee(base, "agent-uuid-1")
    assert base == {"title": "Call client"}


def test_the_assignee_is_looked_up_only_by_email_never_by_name():
    """get_agent_id_by_name_tool would match a person by display name, and a
    near-miss there is a silently misassigned client task.

    Asserted on what the function actually queries rather than on its source
    text — the docstring names the tool it avoids, so grepping the source fails
    on its own explanation.
    """
    supa = _UsersSupa([{"email": "g@rsg.net", "nowcerts_agent_id": "agent-uuid-1"}])
    cw.nowcerts_agent_id(supa, "g@rsg.net")

    assert len(supa.queries) == 1, "one lookup, not a name-resolution round trip"
    table, params = supa.queries[0]
    assert table == "agency_crm_users"
    assert params.get("email") == "eq.g@rsg.net"
    assert not any("name" in k for k in params), (
        f"resolved using {sorted(params)} — assignment must key on the stored id"
    )
