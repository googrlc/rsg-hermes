"""Standalone tasks and the task update lifecycle (issue #195).

Before this, agency_crm_tasks.case_id was NOT NULL with FK -> agency_crm_cases
ON DELETE CASCADE. Every one of the 14 cases was client work, so internal
follow-up ("update commission percentage") had to borrow somebody's case — and
the cascade meant deleting that unrelated case took the internal task with it.

Tasks were also create-only: no update path existed anywhere, and all 18 tasks
in production sat open because nothing could ever close one.
"""

from __future__ import annotations

import pytest

from hermes.renewals import cases as C


class FakeSupa:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.inserts: list[dict] = []
        self.updates: list[tuple[str, dict]] = []
        self.selects: list[tuple[str, dict]] = []
        self._seq = 0

    def select(self, table, *, columns="*", params=None, limit=1000):
        self.selects.append((table, dict(params or {})))
        rows = self.rows
        p = params or {}
        if (cid := p.get("case_id")) is not None:
            if cid == "is.null":
                rows = [r for r in rows if r.get("case_id") is None]
            elif cid == "not.is.null":
                rows = [r for r in rows if r.get("case_id") is not None]
            else:
                rows = [r for r in rows if r.get("case_id") == cid.removeprefix("eq.")]
        if (iid := p.get("insured_database_id")) is not None:
            if iid == "is.null":
                rows = [r for r in rows if r.get("insured_database_id") is None]
            else:
                rows = [r for r in rows if r.get("insured_database_id") == iid.removeprefix("eq.")]
        if (tid := p.get("id")) is not None:
            rows = [r for r in rows if r.get("id") == tid.removeprefix("eq.")]
        if (st := p.get("status")) and st.startswith("not.in."):
            closed = st.removeprefix("not.in.(").rstrip(")").split(",")
            rows = [r for r in rows if r.get("status") not in closed]
        return [dict(r) for r in rows][:limit]

    def insert(self, table, payload):
        self._seq += 1
        row = {"id": f"T{self._seq}", **payload}
        self.rows.append(row)
        self.inserts.append(row)
        return dict(row)

    def update(self, table, record_id, payload):
        self.updates.append((record_id, payload))
        for r in self.rows:
            if r.get("id") == record_id:
                r.update(payload)
                return dict(r)
        return {"id": record_id, **payload}


# --- the three shapes --------------------------------------------------------

def test_a_task_can_exist_with_no_case():
    """The whole point: "update commission percentage" is not client work."""
    supa = FakeSupa()
    created = C.create_tasks(supa, tasks=[{"title": "update commission percentage"}],
                             created_by_email="lamar@risksolutionsgroup.net")
    assert len(created) == 1
    # _compact strips None, so the key is absent rather than explicitly null —
    # equivalent for a nullable column, and .get() asserts what actually matters.
    assert created[0].get("case_id") is None
    assert created[0].get("insured_database_id") is None


def test_a_task_can_name_a_client_without_a_case():
    supa = FakeSupa()
    created = C.create_tasks(supa, insured_database_id="guid-acme",
                             tasks=[{"title": "fix Acme's commission rate"}],
                             created_by_email="lamar@risksolutionsgroup.net")
    assert created[0]["insured_database_id"] == "guid-acme"
    assert created[0].get("case_id") is None


def test_case_linked_creation_still_works():
    supa = FakeSupa()
    created = C.create_tasks(supa, case_id="case-1", tasks=[{"title": "get the COI"}],
                             created_by_email="lamar@risksolutionsgroup.net")
    assert created[0]["case_id"] == "case-1"


# --- dedupe scoping ----------------------------------------------------------

def test_the_same_internal_title_is_not_created_twice_while_open():
    supa = FakeSupa([{"id": "T0", "title": "update commission percentage",
                      "case_id": None, "insured_database_id": None,
                      "status": "not_started"}])
    created = C.create_tasks(supa, tasks=[{"title": "update commission percentage"}])
    assert created == []


def test_a_completed_chore_does_not_block_the_next_one():
    """'update commission percentage' recurs. Deduping on all-time titles would
    make this month's task silently vanish."""
    supa = FakeSupa([{"id": "T0", "title": "update commission percentage",
                      "case_id": None, "insured_database_id": None,
                      "status": "completed"}])
    created = C.create_tasks(supa, tasks=[{"title": "update commission percentage"}])
    assert len(created) == 1


def test_internal_dedupe_does_not_look_at_other_clients_tasks():
    """Same title against a different client is a different job."""
    supa = FakeSupa([{"id": "T0", "title": "fix commission rate", "case_id": None,
                      "insured_database_id": "guid-other", "status": "not_started"}])
    created = C.create_tasks(supa, insured_database_id="guid-acme",
                             tasks=[{"title": "fix commission rate"}])
    assert len(created) == 1


def test_internal_dedupe_ignores_case_linked_tasks_with_the_same_title():
    supa = FakeSupa([{"id": "T0", "title": "get the COI", "case_id": "case-1",
                      "insured_database_id": None, "status": "not_started"}])
    created = C.create_tasks(supa, tasks=[{"title": "get the COI"}])
    assert len(created) == 1


def test_a_dedupe_read_failure_does_not_block_the_create():
    class Broken(FakeSupa):
        def select(self, *a, **k):
            raise RuntimeError("postgrest down")

    supa = Broken()
    created = C.create_tasks(supa, tasks=[{"title": "still needs doing"}])
    assert len(created) == 1


# --- the update lifecycle ----------------------------------------------------

def _task(**kw):
    base = {"id": "T1", "title": "t", "case_id": None, "status": "not_started",
            "priority": "medium", "completed_at": None}
    base.update(kw)
    return base


def test_status_and_priority_are_editable():
    supa = FakeSupa([_task()])
    row = C.update_task(supa, "T1", {"status": "in_progress", "priority": "urgent"})
    assert row["status"] == "in_progress" and row["priority"] == "urgent"


def test_closing_a_task_stamps_completed_at():
    supa = FakeSupa([_task()])
    row = C.update_task(supa, "T1", {"status": "completed"})
    assert row["completed_at"] is not None


def test_cancelling_also_counts_as_closed():
    supa = FakeSupa([_task()])
    row = C.update_task(supa, "T1", {"status": "cancelled"})
    assert row["completed_at"] is not None


def test_reopening_a_task_clears_completed_at():
    """A task showing in_progress with a completion timestamp is the kind of
    contradiction that makes a queue untrustworthy."""
    supa = FakeSupa([_task(status="completed", completed_at="2026-07-01T00:00:00Z")])
    row = C.update_task(supa, "T1", {"status": "in_progress"})
    assert row["completed_at"] is None


def test_completed_at_is_never_taken_from_the_caller():
    supa = FakeSupa([_task()])
    C.update_task(supa, "T1", {"status": "completed", "completed_at": "1999-01-01T00:00:00Z"})
    _, payload = supa.updates[-1]
    assert payload["completed_at"] != "1999-01-01T00:00:00Z"


def test_editing_something_other_than_status_leaves_completed_at_alone():
    supa = FakeSupa([_task(status="completed", completed_at="2026-07-01T00:00:00Z")])
    C.update_task(supa, "T1", {"priority": "high"})
    _, payload = supa.updates[-1]
    assert "completed_at" not in payload


def test_updated_at_is_always_bumped():
    supa = FakeSupa([_task()])
    C.update_task(supa, "T1", {"priority": "low"})
    _, payload = supa.updates[-1]
    assert payload.get("updated_at")


@pytest.mark.parametrize("bad", ["open", "done", "OPEN", "in progress"])
def test_an_unknown_status_is_rejected_by_name(bad):
    """A 400 naming the valid values beats a 502 wrapping a constraint violation."""
    supa = FakeSupa([_task()])
    with pytest.raises(ValueError) as e:
        C.update_task(supa, "T1", {"status": bad})
    assert "not_started" in str(e.value)


@pytest.mark.parametrize("bad", ["critical", "HIGH", "none"])
def test_an_unknown_priority_is_rejected_by_name(bad):
    supa = FakeSupa([_task()])
    with pytest.raises(ValueError) as e:
        C.update_task(supa, "T1", {"priority": bad})
    assert "urgent" in str(e.value)


def test_every_db_allowed_status_is_accepted():
    """Guards drift between TASK_STATUSES and the DB check constraint."""
    for status in C.TASK_STATUSES:
        supa = FakeSupa([_task()])
        assert C.update_task(supa, "T1", {"status": status})["status"] == status


def test_get_task_returns_none_for_a_malformed_id():
    class Broken(FakeSupa):
        def select(self, *a, **k):
            raise RuntimeError("invalid input syntax for type uuid")

    assert C.get_task(Broken(), "not-a-uuid") is None
