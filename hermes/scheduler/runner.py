"""Executor scheduler loop.

Every ``interval`` seconds, ONE lease holder runs a cycle:
  reclaim stalled → requeue/dead-letter failed (backoff) → renewal executor →
  intake executor → quote / casework / opportunity-writeback executors →
  emit structured metrics → alert #systems-check on problems.

Graceful shutdown: SIGTERM/SIGINT stop the loop AFTER the current cycle finishes,
so no claimed job is abandoned mid-flight. Disabled unless SCHEDULER_ENABLED is set.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes.core.queue import DESTINATION_NOWCERTS, QUEUE_TABLE
from hermes.scheduler.locks import LOCKS_TABLE, SchedulerLock
from hermes.scheduler.retry import reclaim_stalled, requeue_or_deadletter

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

LOCK_NAME = "executor_scheduler"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_BATCH = 10

_stop = threading.Event()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _systems_check_channel() -> str:
    """The systems-check destination, as a CATEGORY name.

    The old default "C0AFHN83ZE3" was a Slack channel id absent from team_notify's
    legacy id map, so it resolved to the boss room — dead-lettered jobs, failed
    executors and stalled-queue reclaims were all alerting Lamar's boss room rather
    than a systems feed.
    """
    return (
        os.environ.get("HERMES_SYSTEMS_CHECK_CHANNEL")
        or os.environ.get("HERMES_SLACK_FALLBACK_CHANNEL")
        or "systems"
    )


def _alert(text: str) -> None:
    """Best-effort alert to #systems-check. Never raises into the cycle."""
    try:
        from hermes.integrations.slack_notifier import SlackNotifier

        SlackNotifier(channel=_systems_check_channel()).post_message(text=text)
    except Exception:
        log.exception("scheduler: alert to #systems-check failed: %s", text)


def service_executors(service: str) -> tuple[str, ...]:
    """Which executors a per-service worker runs.

    Only the executors this scheduler actually drives today. The command-center
    intake executor (intake_ams / intake_crm) is deliberately absent: it has
    always been manual, via POST /api/intake/run, and a split is the wrong place
    to quietly start running something on a timer.
    """
    return {
        "renewals": ("renewal",),
        "intake": ("intake",),
        "cases": ("casework",),
        "hub": ("quote", "opportunity_writeback"),
    }.get(service, ())


def run_service_cycle(
    supa: "SupabaseClient",
    *,
    service: str,
    lock: SchedulerLock,
    batch: int = DEFAULT_BATCH,
) -> dict[str, Any]:
    """One cycle for a single service's queue work.

    The difference from `run_one_cycle` is scope, and scope is the whole point:
    this worker takes a lock named for its service (so services do not wait on
    each other), and its retry/reclaim pass is filtered to its own object types
    (so it never dead-letters or reclaims a job belonging to another worker).
    """
    from hermes.services import SERVICES

    if service not in SERVICES:
        raise ValueError(f"unknown service {service!r}")
    object_types = SERVICES[service].queue_object_types
    names = service_executors(service)
    if not object_types or not names:
        log.info("scheduler[%s]: no queue work for this service", service)
        return {"acquired": False, "service": service, "reason": "no queue work"}

    if not lock.acquire():
        log.info("scheduler[%s]: lock held by another replica; skipping cycle", service)
        return {"acquired": False, "service": service}

    started = _utcnow()
    metrics: dict[str, Any] = {
        "acquired": True, "service": service,
        "started_at": started.isoformat(), "owner": lock.owner,
    }
    problems: list[str] = []
    try:
        metrics["stalled"] = reclaim_stalled(supa, now=started, object_types=object_types)
        metrics["retry"] = requeue_or_deadletter(supa, now=started, object_types=object_types)
        lock.renew()

        for name in names:
            try:
                metrics[name] = _run_executor(name, supa=supa, batch=batch)
            except Exception as exc:  # noqa: BLE001 — one executor must not cost the rest
                log.exception("scheduler[%s]: %s executor failed", service, name)
                problems.append(f"{name} executor aborted: {exc}")
            lock.renew()

        if metrics["retry"]["dead"]:
            problems.append(
                f"{metrics['retry']['dead']} job(s) DEAD-LETTERED: {metrics['retry']['dead_ids']}"
            )
        if metrics["stalled"]["reclaimed"]:
            problems.append(
                f"{metrics['stalled']['reclaimed']} stalled job(s) reclaimed: "
                f"{metrics['stalled']['reclaimed_ids']}"
            )
        for name in names:
            if metrics.get(name, {}).get("failed"):
                problems.append(f"{name} executor: {metrics[name]['failed']} failed this pass")
    except Exception as exc:  # noqa: BLE001 — a bad cycle must not kill the loop
        log.exception("scheduler[%s]: cycle crashed", service)
        problems.append(f"scheduler cycle crashed: {exc}")
    finally:
        metrics["finished_at"] = _utcnow().isoformat()
        metrics["problems"] = problems
        lock.release()

    log.info("scheduler_cycle %s", json.dumps(metrics, default=str))
    if problems:
        _alert(f":rotating_light: Hermes {service} worker — " + " | ".join(problems))
    return metrics


def _run_executor(name: str, *, supa: "SupabaseClient", batch: int) -> dict[str, Any]:
    """Run one named executor. NowCerts-bound ones share a client per call so a
    cycle authenticates once."""
    if name == "renewal":
        from hermes.renewals.executor import run_executor

        return run_executor(supa=supa, limit=batch)
    if name == "intake":
        from hermes.intake.executor import run_intake_executor

        return run_intake_executor(supa=supa, limit=batch)

    from hermes.integrations.nowcerts_client import NowCertsClient

    nc = NowCertsClient()
    if name == "quote":
        from hermes.quotes.executor import run_quote_executor

        return run_quote_executor(supa=supa, nowcerts=nc, limit=batch)
    if name == "casework":
        from hermes.casework.executor import run_casework_executor

        return run_casework_executor(supa=supa, nowcerts=nc, limit=batch)
    if name == "opportunity_writeback":
        from hermes.sync.opportunity_writeback import run_opportunity_writeback_executor

        return run_opportunity_writeback_executor(supa=supa, nowcerts=nc, limit=batch)
    raise ValueError(f"unknown executor {name!r}")


def run_one_cycle(
    supa: "SupabaseClient",
    *,
    lock: SchedulerLock,
    batch: int = DEFAULT_BATCH,
) -> dict[str, Any]:
    """One locked cycle over EVERY service's queue work.

    This is the unsplit scheduler and remains the default. A split deployment
    runs `run_service_cycle` per service instead — do not run both against the
    same queue.
    """
    from hermes.casework.executor import run_casework_executor
    from hermes.intake.executor import run_intake_executor
    from hermes.quotes.executor import run_quote_executor
    from hermes.renewals.executor import run_executor
    from hermes.sync.opportunity_writeback import run_opportunity_writeback_executor

    if not lock.acquire():
        log.info("scheduler: lock held by another replica; skipping cycle")
        return {"acquired": False}

    started = _utcnow()
    metrics: dict[str, Any] = {"acquired": True, "started_at": started.isoformat(), "owner": lock.owner}
    problems: list[str] = []
    try:
        metrics["stalled"] = reclaim_stalled(supa, now=started)
        metrics["retry"] = requeue_or_deadletter(supa, now=started)
        lock.renew()

        metrics["renewal"] = run_executor(supa=supa, limit=batch)
        lock.renew()
        metrics["intake"] = run_intake_executor(supa=supa, limit=batch)
        lock.renew()

        # NowCerts-bound executors — the ONLY thing that moves quote / casework /
        # opportunity-writeback jobs to the AMS. One shared client so
        # a cycle authenticates to NowCerts once. Guarded as a group: NowCertsClient()
        # raises when credentials are absent, and that must not cost us the
        # renewal/intake problem detection below.
        try:
            from hermes.integrations.nowcerts_client import NowCertsClient

            nc = NowCertsClient()
            metrics["quote"] = run_quote_executor(supa=supa, nowcerts=nc, limit=batch)
            lock.renew()
            metrics["casework"] = run_casework_executor(supa=supa, nowcerts=nc, limit=batch)
            lock.renew()
            metrics["opportunity_writeback"] = run_opportunity_writeback_executor(
                supa=supa, nowcerts=nc, limit=batch
            )
        except Exception as exc:  # noqa: BLE001 — keep the rest of the cycle's reporting
            log.exception("scheduler: NowCerts executor group failed")
            problems.append(f"NowCerts executors aborted: {exc}")

        # Problem detection -> alerts.
        if metrics["retry"]["dead"]:
            problems.append(f"{metrics['retry']['dead']} job(s) DEAD-LETTERED: {metrics['retry']['dead_ids']}")
        if metrics["stalled"]["reclaimed"]:
            problems.append(f"{metrics['stalled']['reclaimed']} stalled job(s) reclaimed: {metrics['stalled']['reclaimed_ids']}")
        if metrics["renewal"].get("failed"):
            problems.append(f"renewal executor: {metrics['renewal']['failed']} failed this pass")
        if metrics["intake"].get("failed"):
            problems.append(f"intake executor: {metrics['intake']['failed']} failed this pass")
        for name in ("quote", "casework", "opportunity_writeback"):
            if metrics.get(name, {}).get("failed"):
                problems.append(f"{name} executor: {metrics[name]['failed']} failed this pass")
    except Exception as exc:  # noqa: BLE001 — a bad cycle must not kill the loop
        log.exception("scheduler: cycle crashed")
        problems.append(f"scheduler cycle crashed: {exc}")
    finally:
        metrics["finished_at"] = _utcnow().isoformat()
        metrics["problems"] = problems
        lock.release()

    # Structured run metrics = audit log line (captured by docker logs).
    log.info("scheduler_cycle %s", json.dumps(metrics, default=str))
    if problems:
        _alert(":rotating_light: Hermes executor scheduler — " + " | ".join(problems))
    return metrics


def run_scheduler_loop(
    *,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    batch: int = DEFAULT_BATCH,
    supa: "SupabaseClient | None" = None,
    service: str | None = None,
) -> None:
    """Continuous scheduler. Runs until SIGTERM/SIGINT, finishing the in-flight cycle.

    With ``service``, this is that service's own worker: its own lock, its own
    object types, only its executors. Without it, the single scheduler that
    drives everything — still the default.

    Run one or the other against a queue, never both: the unsplit scheduler
    claims every object type, so it would race the per-service workers for the
    same rows.
    """
    if supa is None:
        from hermes.integrations.supabase_client import SupabaseClient

        supa = SupabaseClient()

    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())

    # Lease TTL just under the interval so a dead holder is replaced next cycle.
    # A per-service worker locks on its own name so services never queue behind
    # each other for the lease.
    lock_name = f"{LOCK_NAME}_{service}" if service else LOCK_NAME
    lock = SchedulerLock(supa, lock_name, ttl_seconds=max(30, interval_seconds - 10))
    log.info(
        "scheduler[%s]: starting (interval=%ss, batch=%s, owner=%s)",
        service or "all", interval_seconds, batch, lock.owner,
    )

    while not _stop.is_set():
        try:
            if service:
                run_service_cycle(supa, service=service, lock=lock, batch=batch)
            else:
                run_one_cycle(supa, lock=lock, batch=batch)
        except Exception:  # noqa: BLE001
            log.exception("scheduler[%s]: unexpected error at loop level", service or "all")
        # Interruptible wait — a shutdown signal wakes us immediately.
        _stop.wait(interval_seconds)

    log.info(
        "scheduler[%s]: graceful shutdown (in-flight cycle completed; no jobs abandoned)",
        service or "all",
    )


def scheduler_health(supa: "SupabaseClient") -> dict[str, Any]:
    """Point-in-time health for a health check: NowCerts queue depths + lock state."""
    def _count(status: str) -> int:
        try:
            rows = supa.select(
                QUEUE_TABLE, columns="id",
                params={"destination_system": f"eq.{DESTINATION_NOWCERTS}", "status": f"eq.{status}"},
                limit=1000,
            )
            return len(rows)
        except Exception:
            return -1

    lock_rows = []
    try:
        lock_rows = supa.select(LOCKS_TABLE, columns="lock_name,owner,expires_at",
                                params={"lock_name": f"eq.{LOCK_NAME}"}, limit=1)
    except Exception:
        pass
    return {
        "queue": {s: _count(s) for s in ("queued", "processing", "failed", "dead")},
        "lock": lock_rows[0] if lock_rows else None,
        "checked_at": _utcnow().isoformat(),
    }
