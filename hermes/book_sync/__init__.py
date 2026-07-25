"""Book-sync health checks — verify NowCerts and the canonical book agree.

This package compares the *book of business* held in the AMS (NowCerts, the
system of record) against the Supabase mirror the cockpit reads, and surfaces
drift. It is read-only by design; corrections go through the approval-gated
outbound_sync_queue executors.

See `health.py` for the main entrypoint used by /api/hermes/book-sync.
"""

from hermes.book_sync.health import (
    BookSyncReport,
    CarrierBreakdown,
    DriftCheck,
    run_book_sync_health,
)

__all__ = [
    "BookSyncReport",
    "CarrierBreakdown",
    "DriftCheck",
    "run_book_sync_health",
]
