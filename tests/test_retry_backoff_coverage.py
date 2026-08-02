"""The retry backoff must be honoured by every executor that can be backed off.

`retry.py` requeues a failed job with ``scheduled_for = now + backoff``. That only
delays anything if the executor which picks the job up FILTERS on scheduled_for.

Until 2026-07-26, `_OBJECT_TYPES` held exactly (renewal, intake) — which happened
to be precisely the two executors that honoured the column. The system was
accidentally consistent, and the coupling was invisible: adding 'quote' to that
tuple (the obvious next step, since the quote executor sets status=failed) would
have produced an exponential backoff that silently did nothing, hammering NowCerts
every scheduler cycle forever.

These tests make the pairing explicit so it cannot drift back.
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone

import pytest

from hermes.scheduler import retry as R

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

# Every module with an eligibility query that CLAIMS queue work.
EXECUTOR_MODULES = (
    "hermes.quotes.executor",
    "hermes.renewals.executor",
    "hermes.intake.executor",
    "hermes.command_center.intake_executor",
    "hermes.sync.opportunity_writeback",
)


def _source(module_path: str) -> str:
    import importlib

    return inspect.getsource(importlib.import_module(module_path))


# --- the pairing -------------------------------------------------------------

@pytest.mark.parametrize("module_path", EXECUTOR_MODULES)
def test_every_executor_honours_the_backoff(module_path):
    """A claiming executor must filter on scheduled_for, or a backed-off job is
    retried immediately, every cycle, forever."""
    src = _source(module_path)
    assert "due_filter()" in src or "scheduled_for.is.null" in src, (
        f"{module_path} claims queue work without honouring scheduled_for — "
        "its backoff would be a no-op"
    )


def test_the_human_review_read_is_deliberately_exempt():
    """renewals/writeback._proposed lists rows awaiting a human decision. Hiding a
    backed-off row there would make it invisible to whoever must approve it."""
    src = _source("hermes.renewals.writeback")
    assert "does NOT apply retry.due_filter()" in src


def _claimed_object_types() -> set[str]:
    """Object types the executors actually handle, read from their constants.

    Resolved from module attributes rather than by grepping source: the executors
    reference these via imported names (OBJECT_TYPE_CRM lives in router.py), so a
    text search reports a false gap.
    """
    import importlib

    claimed: set[str] = set()
    for path in EXECUTOR_MODULES + ("hermes.command_center.router", "hermes.intake.commit"):
        mod = importlib.import_module(path)
        for name in dir(mod):
            if name.startswith("OBJECT_TYPE"):
                value = getattr(mod, name)
                if isinstance(value, str):
                    claimed.add(value)
    return claimed


def test_every_backed_off_object_type_is_claimed_by_some_executor():
    """A type in _OBJECT_TYPES with no executor is a job that fails, backs off, and
    is then never retried by anyone.

    Departed types are exempt from *this* codebase having an executor, but not
    from the rule — their executor lives in the app repo that took them, and
    `test_a_departed_type_is_not_retried_here` checks this scheduler has
    correspondingly stopped retrying them. The two together are the same
    guarantee the single assertion used to make.
    """
    from hermes.scheduler.runner import DEPARTED_OBJECT_TYPES

    claimed = _claimed_object_types()
    for object_type in R._OBJECT_TYPES:
        if object_type in DEPARTED_OBJECT_TYPES:
            continue
        assert object_type in claimed, (
            f"object_type {object_type!r} is backed off but no executor claims it"
        )


def test_a_departed_type_is_not_retried_here():
    """Retrying is as much ownership as draining.

    A scheduler that dead-letters a `case` row has decided another service's job
    is finished; one that re-queues a row mid-write hands it back while the owner
    still holds it. So a type whose executor left must also leave the unsplit
    cycle's retry scope — the two must move together, and this is what fails if
    only one of them does.
    """
    from hermes.scheduler.runner import DEPARTED_OBJECT_TYPES, HUB_OBJECT_TYPES

    assert DEPARTED_OBJECT_TYPES, "nothing has departed; this test is vacuous"
    for object_type in DEPARTED_OBJECT_TYPES:
        assert object_type in R._OBJECT_TYPES, (
            f"{object_type!r} left the queue contract entirely — the app that took "
            "it still needs its backoff honoured"
        )
        assert object_type not in HUB_OBJECT_TYPES, (
            f"the unsplit scheduler still retries {object_type!r} after its executor left"
        )
        assert object_type not in _claimed_object_types(), (
            f"{object_type!r} is declared departed but an executor here still claims it"
        )


def test_intake_crm_is_backed_off_and_the_destination_filter_covers_it():
    """CRM-destination jobs (object_type='intake_crm', destination_system='crm') used
    to be silently dropped by requeue_or_deadletter because it filtered
    destination_system='nowcerts'. Both the object type and the destination filter
    must now cover CRM so failures back off and dead-letter like any other."""
    from hermes.command_center.router import OBJECT_TYPE_CRM

    assert OBJECT_TYPE_CRM in R._OBJECT_TYPES
    # The destination filter must include both destinations, not just nowcerts.
    assert "eq.nowcerts" not in R._DESTINATION_FILTER, (
        "destination filter still hardcoded to nowcerts — CRM failures would be dropped"
    )
    assert "crm" in R._DESTINATION_FILTER
    assert "nowcerts" in R._DESTINATION_FILTER


def test_backoff_covers_the_object_types_that_can_actually_fail():
    """Regression guard on the original gap: these all set status=failed."""
    for expected in ("renewal", "intake", "quote", "case", "task",
                     "opportunity_writeback", "intake_ams", "intake_crm"):
        assert expected in R._OBJECT_TYPES


# --- the shared filter -------------------------------------------------------

def test_due_filter_admits_unscheduled_and_past_due_rows():
    f = R.due_filter(NOW)
    assert "scheduled_for.is.null" in f["or"]
    assert f"scheduled_for.lte.{NOW.isoformat()}" in f["or"]


def test_due_filter_is_a_single_postgrest_or_clause():
    """It merges into an existing params dict via **, so it must not collide with
    the status/object_type keys already there."""
    f = R.due_filter(NOW)
    assert set(f) == {"or"}


def test_due_filter_defaults_to_now():
    before = datetime.now(timezone.utc)
    stamp = re.search(r"lte\.([^)]+)\)", R.due_filter()["or"]).group(1)
    parsed = datetime.fromisoformat(stamp)
    assert before <= parsed <= datetime.now(timezone.utc)


# --- backoff maths, since it now governs six executors ----------------------

def test_backoff_grows_then_caps():
    delays = [R.compute_backoff_seconds(n) for n in range(1, 12)]
    assert delays == sorted(delays)
    assert delays[0] < delays[3]
    assert delays[-1] == R.BACKOFF_CAP_SECONDS


def test_backoff_is_never_zero():
    """A zero delay would make scheduled_for immediately due and the backoff moot."""
    assert all(R.compute_backoff_seconds(n) > 0 for n in range(0, 10))


# --- requeue writes the column the filter reads -----------------------------

class FakeSupa:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[tuple[str, dict]] = []

    def select(self, table, *, columns="*", params=None, limit=1000):
        return [dict(r) for r in self.rows]

    def update(self, table, record_id, payload):
        self.updates.append((record_id, dict(payload)))
        return {"id": record_id, **payload}


def test_a_requeued_job_gets_a_future_scheduled_for():
    supa = FakeSupa([{"id": "q1", "object_type": "quote", "object_id": "o1", "attempt_count": 1}])
    out = R.requeue_or_deadletter(supa, now=NOW)
    assert out["requeued"] == 1
    _, payload = supa.updates[-1]
    assert payload["status"] == R.QUEUE_QUEUED
    assert datetime.fromisoformat(payload["scheduled_for"]) > NOW


def test_a_job_past_the_cap_is_dead_lettered_not_rescheduled():
    supa = FakeSupa([{"id": "q9", "object_type": "quote", "object_id": "o9",
                      "attempt_count": R.MAX_ATTEMPTS}])
    out = R.requeue_or_deadletter(supa, now=NOW)
    assert out["dead"] == 1 and out["dead_ids"] == ["q9"]
    _, payload = supa.updates[-1]
    assert payload["status"] == R.QUEUE_DEAD
    assert "scheduled_for" not in payload


def test_a_backed_off_job_is_not_yet_due():
    """End to end on the contract: what requeue writes, due_filter must exclude."""
    supa = FakeSupa([{"id": "q1", "object_type": "quote", "object_id": "o1", "attempt_count": 1}])
    R.requeue_or_deadletter(supa, now=NOW)
    _, payload = supa.updates[-1]
    scheduled = datetime.fromisoformat(payload["scheduled_for"])
    # The filter admits rows with scheduled_for <= now; this one is in the future.
    assert scheduled > NOW
    assert scheduled <= NOW + timedelta(seconds=R.BACKOFF_CAP_SECONDS)


def test_a_crm_destination_job_is_requeued_with_backoff():
    """A CRM-destination (intake_crm) failure must be requeued on the same backoff
    schedule as a NowCerts write, not left to sit at status=failed forever."""
    from hermes.command_center.router import OBJECT_TYPE_CRM

    supa = FakeSupa([{"id": "qc1", "object_type": OBJECT_TYPE_CRM,
                      "object_id": "sub_1", "attempt_count": 0}])
    out = R.requeue_or_deadletter(supa, now=NOW)
    assert out["requeued"] == 1
    _, payload = supa.updates[-1]
    assert payload["status"] == R.QUEUE_QUEUED
    assert payload["attempt_count"] == 1
    assert datetime.fromisoformat(payload["scheduled_for"]) > NOW


def test_reclaim_stalled_uses_the_shared_destination_filter():
    """reclaim_stalled must not drop CRM-destination stalled jobs either."""
    src = _source("hermes.scheduler.retry")
    # reclaim_stalled should reference the shared filter, not a hardcoded nowcerts eq.
    assert "_DESTINATION_FILTER" in src
    assert 'eq.{DESTINATION_NOWCERTS}' not in src
