"""Tests for the executor scheduler: lease lock, backoff, requeue/dead-letter,
stalled reclaim, and one locked cycle. Supabase mocked."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
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


@contextmanager
def _nowcerts_group(**overrides):
    """Patch the NowCerts client + its three executors. Yields the executor mocks.

    NowCertsClient() raises without credentials, so every cycle test has to stub it.
    """
    clean = {"claimed": 0, "completed": 0, "failed": 0, "previews": []}
    targets = {
        "quote": "hermes.quotes.executor.run_quote_executor",
        "casework": "hermes.casework.executor.run_casework_executor",
        "opportunity_writeback": "hermes.sync.opportunity_writeback.run_opportunity_writeback_executor",
    }
    with ExitStack() as stack:
        stack.enter_context(patch("hermes.sync.nowcerts_client.NowCertsClient"))
        yield {
            name: stack.enter_context(
                patch(target, return_value=overrides.get(name, dict(clean)))
            )
            for name, target in targets.items()
        }


def test_cycle_skips_when_lock_not_acquired():
    m = run_one_cycle(MagicMock(), lock=_lock(acquired=False))
    assert m == {"acquired": False}


def test_cycle_runs_all_executors_and_releases_lock():
    supa = MagicMock()
    supa.select.return_value = []          # no failed/stalled jobs
    lk = _lock()
    with patch("hermes.renewals.executor.run_executor",
               return_value={"claimed": 1, "completed": 1, "failed": 0, "blocked": 0}) as rex, \
         patch("hermes.intake.executor.run_intake_executor",
               return_value={"claimed": 0, "completed": 0, "failed": 0, "previews": []}) as iex, \
         _nowcerts_group() as nc, \
         patch("hermes.scheduler.runner._alert") as alert:
        m = run_one_cycle(supa, lock=lk)
    rex.assert_called_once(); iex.assert_called_once()
    # The NowCerts-bound executors are the only path off outbound_sync_queue for
    # these job types.
    for name, mock in nc.items():
        mock.assert_called_once()
        assert name in m
    lk.release.assert_called_once()
    assert m["acquired"] is True and m["problems"] == []
    alert.assert_not_called()


def test_nowcerts_executors_share_one_client():
    """One client per cycle so we authenticate to NowCerts once, not three times."""
    supa = MagicMock()
    supa.select.return_value = []
    with patch("hermes.renewals.executor.run_executor", return_value={"failed": 0}), \
         patch("hermes.intake.executor.run_intake_executor", return_value={"failed": 0}), \
         _nowcerts_group() as nc, \
         patch("hermes.scheduler.runner._alert"):
        run_one_cycle(supa, lock=_lock())
    clients = {mock.call_args.kwargs["nowcerts"] for mock in nc.values()}
    assert len(clients) == 1


def test_cycle_alerts_on_nowcerts_executor_failure():
    supa = MagicMock()
    supa.select.return_value = []
    with patch("hermes.renewals.executor.run_executor", return_value={"failed": 0}), \
         patch("hermes.intake.executor.run_intake_executor", return_value={"failed": 0}), \
         _nowcerts_group(quote={"claimed": 2, "completed": 1, "failed": 1}), \
         patch("hermes.scheduler.runner._alert") as alert:
        m = run_one_cycle(supa, lock=_lock())
    assert any("quote executor: 1 failed" in p for p in m["problems"])
    alert.assert_called_once()


def test_missing_nowcerts_credentials_does_not_hide_renewal_problems():
    """NowCertsClient() raises without creds; renewal/intake alerting must survive."""
    supa = MagicMock()
    supa.select.return_value = []
    with patch("hermes.renewals.executor.run_executor", return_value={"failed": 3}), \
         patch("hermes.intake.executor.run_intake_executor", return_value={"failed": 0}), \
         patch("hermes.sync.nowcerts_client.NowCertsClient", side_effect=RuntimeError("no creds")), \
         patch("hermes.scheduler.runner._alert") as alert:
        m = run_one_cycle(supa, lock=_lock())
    assert any("NowCerts executors aborted" in p for p in m["problems"])
    assert any("renewal executor: 3 failed" in p for p in m["problems"])
    alert.assert_called_once()


def test_cycle_alerts_on_deadletter():
    supa = MagicMock()
    lk = _lock()
    with patch("hermes.renewals.executor.run_executor", return_value={"failed": 0}), \
         patch("hermes.intake.executor.run_intake_executor", return_value={"failed": 0}), \
         _nowcerts_group(), \
         patch("hermes.scheduler.runner.reclaim_stalled", return_value={"reclaimed": 0, "reclaimed_ids": []}), \
         patch("hermes.scheduler.runner.requeue_or_deadletter", return_value={"requeued": 0, "dead": 1, "dead_ids": ["d1"]}), \
         patch("hermes.scheduler.runner._alert") as alert:
        m = run_one_cycle(supa, lock=lk)
    assert any("DEAD-LETTERED" in p for p in m["problems"])
    alert.assert_called_once()
