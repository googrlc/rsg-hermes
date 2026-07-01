"""Book-sync health checks — verify NowCerts ↔ EspoCRM ↔ Supabase agreement.

This package compares the *book of business* across the three systems and
surfaces drift. It is read-only by design; writes go through the existing
crm_write_queue / proposals pipeline.

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
