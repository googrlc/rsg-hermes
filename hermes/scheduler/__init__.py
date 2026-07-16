"""Executor scheduler — runs the intake + renewal executors on a fixed cadence
with single-instance locking, bounded retry/backoff + dead-letter, structured run
metrics, failure/stalled-queue alerts, and graceful shutdown.

Disabled by default: the runner only starts when SCHEDULER_ENABLED is truthy.
"""
