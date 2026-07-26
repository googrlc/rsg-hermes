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
    "hermes.casework.executor",
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
    is then never retried by anyone."""
    claimed = _claimed_object_types()
    for object_type in R._OBJECT_TYPES:
        assert object_type in claimed, (
            f"object_type {object_type!r} is backed off but no executor claims it"
        )


def test_intake_crm_is_excluded_because_the_destination_filter_would_drop_it():
    """requeue_or_deadletter filters destination_system='nowcerts'. A CRM-destination
    job carries 'crm', so listing it would look like coverage while every row was
    silently excluded."""
    from hermes.command_center.router import OBJECT_TYPE_CRM

    assert OBJECT_TYPE_CRM not in R._OBJECT_TYPES
    assert "intake_crm is deliberately absent" in _source("hermes.scheduler.retry")


def test_backoff_covers_the_object_types_that_can_actually_fail():
    """Regression guard on the original gap: these all set status=failed."""
    for expected in ("renewal", "intake", "quote", "case", "task",
                     "opportunity_writeback", "intake_ams"):
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
