"""Hermes lane scheduler — in-process cron for all scheduled lanes.

One long-running process (kept alive by the com.rsg.hermes.scheduler
LaunchAgent). Every lane registers its schedule in JOBS below; adding a
lane job is one line. Stdlib only — no APScheduler dependency.

Catch-up rule: if the Mac was asleep at a job's time, the job runs on wake
as long as it hasn't already run that day (state in ~/.hermes/scheduler_state.json).

Run: python -m hermes.scheduler
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("hermes.scheduler")
STATE_PATH = Path.home() / ".hermes" / "scheduler_state.json"
POLL_SECONDS = 30


@dataclass
class Job:
    name: str
    at: str                      # "HH:MM" local time
    func: Callable[[], object]
    weekdays: set[int] = field(default_factory=lambda: {0, 1, 2, 3, 4, 5, 6})
    enabled_env: str | None = None   # env var that must be "1" to run

    @property
    def enabled(self) -> bool:
        if self.enabled_env is None:
            return True
        return os.environ.get(self.enabled_env, "0") == "1"


def _run_morning_digest():
    from hermes.digest import deliver, sweep
    data = sweep.collect()
    deliver.post(data)
    return {"policies": len(data["policies"]), "quiet": len(data["quiet"]),
            "tasks": len(data["tasks"])}


def _run_renewal_sweep():
    from hermes.renewals.sweep import run
    return run()


# ─── Lane job registry ──────────────────────────────────────────────────
# Boss lane: read-only morning digest -> #the-boss, daily 7:00 AM
# Gretchen lane: renewal sweep -> tasks + #gretchen-tasks cards, weekdays 6:00 AM
#   (writes to EspoCRM, so it ships OFF until HERMES_RENEWAL_SWEEP_ENABLED=1)
JOBS: list[Job] = [
    Job(name="morning_digest", at="07:00", func=_run_morning_digest),
    Job(name="renewal_sweep", at="06:00", func=_run_renewal_sweep,
        weekdays={0, 1, 2, 3, 4}, enabled_env="HERMES_RENEWAL_SWEEP_ENABLED"),
]


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1))


def _due(job: Job, now: datetime, state: dict) -> bool:
    if not job.enabled or now.weekday() not in job.weekdays:
        return False
    if state.get(job.name) == now.date().isoformat():
        return False                      # already ran today
    hh, mm = (int(x) for x in job.at.split(":"))
    scheduled = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    # due now, or wake-catch-up — but never more than 6h late (a 9 PM
    # "morning digest" helps nobody)
    return scheduled <= now <= scheduled + timedelta(hours=6)


def tick(now: datetime | None = None, state: dict | None = None) -> list[str]:
    """One scheduler pass. Returns names of jobs run (unit-testable)."""
    now = now or datetime.now()
    state = state if state is not None else _load_state()
    ran = []
    for job in JOBS:
        if not _due(job, now, state):
            continue
        state[job.name] = now.date().isoformat()
        _save_state(state)               # mark BEFORE running: no crash-loops
        log.info("Running %s", job.name)
        try:
            result = job.func()
            log.info("%s done: %s", job.name, result)
        except Exception:
            log.exception("%s failed (will not retry until tomorrow)", job.name)
        ran.append(job.name)
    return ran


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("Hermes scheduler up. Jobs: %s",
             [(j.name, j.at, "on" if j.enabled else "OFF") for j in JOBS])
    while True:
        try:
            tick()
        except Exception:
            log.exception("tick crashed; continuing")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
