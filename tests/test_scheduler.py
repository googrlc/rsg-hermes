"""Tests for the executor scheduler: lease lock, backoff, requeue/dead-letter,
stalled reclaim, and one locked cycle. Supabase mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermes.scheduler import retry
from hermes.scheduler.locks import SchedulerLock
from hermes.scheduler.runner import run_one_cycle


# ------------------------------------------------------------- lock

def test_lock_acquire_takes_over_expired():
    supa = MagicMock()
    supa.update_where.return_value = [{"lock_name": "x"}]   # expired/own lease grabbed
    assert SchedulerLock(supa, "x").acquire() is True
    supa.insert.assert_not_called()


def test_lock_acquire_first_claim_via_insert():
    supa = MagicMock()
    supa.update_where.return_value = []       # no row yet
    supa.insert.return_value = {"lock_name": "x"}
    assert SchedulerLock(supa, "x").acquire() is True
    supa.insert.assert_called_once()


def test_lock_acquire_loses_race_on_insert_conflict():
    supa = MagicMock()
    supa.update_where.return_value = []
    supa.insert.side_effect = Exception("unique violation")   # another replica holds it
    assert SchedulerLock(supa, "x").acquire() is False


def test_lock_renew_and_release():
    supa = MagicMock()
    supa.update_where.return_value = [{"lock_name": "x"}]
    lk = SchedulerLock(supa, "x")
    lk.acquire()
    assert lk.renew() is True
    lk.release()   # sets held False + expires the lease
    assert lk._held is False


# ------------------------------------------------------------- backoff

def test_compute_backoff_is_exponential_and_capped():
    assert retry.compute_backoff_seconds(1) == 60
    assert retry.compute_backoff_seconds(2) == 120
    assert retry.compute_backoff_seconds(4) == 480
    assert retry.compute_backoff_seconds(20) == retry.BACKOFF_CAP_SECONDS


# ------------------------------------------------------------- requeue / dead-letter

def test_requeue_under_cap_and_deadletter_at_cap():
    supa = MagicMock()
    supa.select.return_value = [
        {"id": "young", "attempt_count": 0},                     # -> requeue
        {"id": "old", "attempt_count": retry.MAX_ATTEMPTS - 1},  # -> dead-letter
    ]
    out = retry.requeue_or_deadletter(supa)
    assert out["requeued"] == 1 and out["dead"] == 1 and out["dead_ids"] == ["old"]
    statuses = {c.args[1]: c.args[2]["status"] for c in supa.update.call_args_list}
    assert statuses["young"] == "queued" and statuses["old"] == "dead"
    # requeued row carries a future scheduled_for
    young = next(c.args[2] for c in supa.update.call_args_list if c.args[1] == "young")
    assert "scheduled_for" in young and young["attempt_count"] == 1


def test_reclaim_stalled_resets_to_queued():
    supa = MagicMock()
    supa.select.return_value = [{"id": "stuck"}]
    out = retry.reclaim_stalled(supa)
    assert out["reclaimed"] == 1 and out["reclaimed_ids"] == ["stuck"]
    assert supa.update.call_args.args[2]["status"] == "queued"


# ------------------------------------------------------------- runner cycle

def _lock(acquired=True):
    lk = MagicMock()
    lk.acquire.return_value = acquired
    lk.owner = "test-owner"
    return lk


def test_cycle_skips_when_lock_not_acquired():
    m = run_one_cycle(MagicMock(), lock=_lock(acquired=False))
    assert m == {"acquired": False}


def test_cycle_runs_both_executors_and_releases_lock():
    supa = MagicMock()
    supa.select.return_value = []          # no failed/stalled jobs
    lk = _lock()
    with patch("hermes.renewals.executor.run_executor",
               return_value={"claimed": 1, "completed": 1, "failed": 0, "blocked": 0}) as rex, \
         patch("hermes.intake.executor.run_intake_executor",
               return_value={"claimed": 0, "completed": 0, "failed": 0, "previews": []}) as iex, \
         patch("hermes.scheduler.runner._alert") as alert:
        m = run_one_cycle(supa, lock=lk)
    rex.assert_called_once(); iex.assert_called_once()
    lk.release.assert_called_once()
    assert m["acquired"] is True and m["problems"] == []
    alert.assert_not_called()


def test_cycle_alerts_on_deadletter():
    supa = MagicMock()
    lk = _lock()
    with patch("hermes.renewals.executor.run_executor", return_value={"failed": 0}), \
         patch("hermes.intake.executor.run_intake_executor", return_value={"failed": 0}), \
         patch("hermes.scheduler.runner.reclaim_stalled", return_value={"reclaimed": 0, "reclaimed_ids": []}), \
         patch("hermes.scheduler.runner.requeue_or_deadletter", return_value={"requeued": 0, "dead": 1, "dead_ids": ["d1"]}), \
         patch("hermes.scheduler.runner._alert") as alert:
        m = run_one_cycle(supa, lock=lk)
    assert any("DEAD-LETTERED" in p for p in m["problems"])
    alert.assert_called_once()
