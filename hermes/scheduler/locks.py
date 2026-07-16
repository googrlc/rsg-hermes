"""Lease-based single-instance lock over Supabase/PostgREST.

Only the current lease holder runs a scheduler cycle. The lease has a short TTL
and is renewed while working; if the holder dies, the lease expires and another
replica can acquire it. No direct Postgres connection is needed — acquisition is
a conditional update on expiry plus an insert-on-conflict for the first claim.
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

LOCKS_TABLE = "scheduler_locks"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_owner() -> str:
    """Stable-per-process holder id: host:pid:nonce."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


class SchedulerLock:
    def __init__(
        self,
        supa: "SupabaseClient",
        name: str,
        *,
        ttl_seconds: int = 290,
        owner: str | None = None,
    ) -> None:
        self.supa = supa
        self.name = name
        self.ttl = ttl_seconds
        self.owner = owner or make_owner()
        self._held = False

    def acquire(self) -> bool:
        """Grab the lock if free/expired (or already ours). Returns True iff held."""
        now = _utcnow()
        expires = (now + timedelta(seconds=self.ttl)).isoformat()
        payload = {"owner": self.owner, "acquired_at": now.isoformat(),
                   "expires_at": expires, "updated_at": now.isoformat()}
        # 1. Take over an expired lease (or re-take our own).
        try:
            updated = self.supa.update_where(
                LOCKS_TABLE, payload,
                filters={"lock_name": f"eq.{self.name}",
                         "or": f"(expires_at.lt.{now.isoformat()},owner.eq.{self.owner})"},
            )
            if updated:
                self._held = True
                return True
        except Exception:
            log.exception("scheduler lock: update-on-acquire failed for %s", self.name)
        # 2. No row yet → first claim. Unique PK means only one replica wins.
        try:
            self.supa.insert(LOCKS_TABLE, {"lock_name": self.name, **payload})
            self._held = True
            return True
        except Exception:
            # Unique violation (someone holds a live lease) or transient error.
            self._held = False
            return False

    def renew(self) -> bool:
        """Extend the lease while working; only succeeds if we still own it."""
        now = _utcnow()
        try:
            updated = self.supa.update_where(
                LOCKS_TABLE,
                {"expires_at": (now + timedelta(seconds=self.ttl)).isoformat(),
                 "updated_at": now.isoformat()},
                filters={"lock_name": f"eq.{self.name}", "owner": f"eq.{self.owner}"},
            )
            return bool(updated)
        except Exception:
            log.exception("scheduler lock: renew failed for %s", self.name)
            return False

    def release(self) -> None:
        """Release the lease (expire it now) if we own it — lets the next cycle start immediately."""
        if not self._held:
            return
        now = _utcnow()
        try:
            self.supa.update_where(
                LOCKS_TABLE,
                {"expires_at": now.isoformat(), "updated_at": now.isoformat()},
                filters={"lock_name": f"eq.{self.name}", "owner": f"eq.{self.owner}"},
            )
        except Exception:
            log.exception("scheduler lock: release failed for %s", self.name)
        finally:
            self._held = False

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
