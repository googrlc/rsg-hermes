"""Live policy book — read the AMS instead of the ``canonical_policies`` mirror.

``canonical_policies`` is a mirror of the NowCerts book maintained by nightly
importers. Mirroring it is what allowed the 2026-07-24 incident: two importers
wrote the table with different scopes, and the narrower one flagged everything
outside its scope as deleted, hiding live renewals from the desk. Reading the
book from the AMS removes that failure mode rather than guarding against it.
The whole book is ~440 policies (~1.5MB), so a full pull is cheap.

WHAT STAYS IN SUPABASE. ``renewed_policy`` is a lineage pointer the NowCerts API
does not expose (see ``canonical_book_sync`` and ``candidate_refresh``), and
eligibility root-walking depends on it. It is not an AMS fact — it is derived
work state — so it keeps living in Supabase and is joined onto the live rows
here. Everything else (premium, dates, carrier, status, active) comes from the AMS.

USAGE. ``select_policies`` is a drop-in for
``supa.select("canonical_policies", columns=..., params=..., limit=...)`` and
supports the PostgREST filters the call sites actually use: ``eq.``, ``in.(...)``
and ``order``. With ``HERMES_AMS_LIVE_READS`` unset it delegates straight to
Supabase, so the cutover is reversible per-deploy.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
import time
from typing import TYPE_CHECKING, Any

from hermes.sync.canonical_book_sync import (
    POLICIES_TABLE,
    POLICY_KEY,
    _map_policy_volatile,
    _num,
    _policy_guid,
)

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.sync.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)

# --- Canonical book ownership -------------------------------------------------
# canonical_policies had two writers and no way to say which owned a row.
# `rsg-import` (pg_cron) pulled only is_quote=false and tombstoned everything
# absent from that pull — including rows it never created. 43 of the 48 tombstoned
# rows belong to the csv-import load, against 5 of its own. Disabled 2026-07-24.
#
# The rule, now that sync_owner exists: ANY writer may refresh volatile fields on
# any row; only the OWNER may deactivate or tombstone one.
TOMBSTONE_PREFIX = "Inactive: not in NowCerts"

OWNER_BOOK_SYNC = "book_sync"
OWNER_RSG_IMPORT = "rsg-import"
OWNER_CSV_IMPORT = "csv-import"


def is_tombstoned(policy: dict[str, Any]) -> bool:
    """True for the phantom 'not in NowCerts' rows the disabled importer wrote.

    Single home for this check. It previously existed as two independent copies —
    agency_snapshot.py and commissions/surface.py each carried the magic string —
    which means any consumer that forgot to check silently counted phantom rows as
    real book.
    """
    return str(policy.get("status") or "").startswith(TOMBSTONE_PREFIX)


def may_deactivate(policy: dict[str, Any], writer: str) -> bool:
    """Whether *writer* is allowed to tombstone or deactivate *policy*.

    An unowned row (sync_owner null, pre-migration) is claimable — refusing there
    would freeze legacy rows permanently. A row owned by someone else is not: that
    is the exact write that corrupted the book in July.
    """
    owner = str(policy.get("sync_owner") or "").strip()
    return not owner or owner == writer


LINEAGE_TABLE = "policy_lineage"
LINEAGE_FIELD = "renewed_policy"

DEFAULT_TTL_SECONDS = 300
_PAGE_SIZE = 100
_MAX_PAGES = 50  # 50 * 100 = 5000, ~11x the current book

_cache: dict[str, Any] = {
    "rows": None, "at": 0.0, "failed_at": 0.0, "refreshing": False, "thread": None, "full_at": 0.0,
}
_lock = threading.Lock()

# How long a failed AMS pull suppresses the next attempt. Long enough that a sick
# upstream isn't re-probed once per dashboard widget, short enough that recovery
# is picked up without a restart.
_FAIL_BACKOFF_SECONDS = 60.0

# How often to re-pull the whole book rather than a delta. A ``changeDate ge``
# query returns changed and new policies but says nothing about deleted ones, so
# without this a removal would sit in the cache indefinitely.
_FULL_REFRESH_DEFAULT = 6 * 3600.0


def _full_refresh_secs() -> float:
    """Read per call, not at import — otherwise it can't be configured or tested."""
    try:
        return max(0.0, float(os.environ.get("HERMES_AMS_FULL_REFRESH", _FULL_REFRESH_DEFAULT)))
    except (TypeError, ValueError):
        return _FULL_REFRESH_DEFAULT


def _iso(ts: float) -> str:
    """UTC ISO-8601 for an OData ``changeDate ge`` filter."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AmsBookUnavailable(RuntimeError):
    """The live book can't be read right now (recent failure, still backing off).

    Its own type so callers can tell "AMS is briefly unavailable, use the mirror"
    apart from a genuine bug. ``select_policies`` already falls back to Supabase
    on any exception, so this degrades to the mirror rather than to an error."""


def live_reads_enabled() -> bool:
    """True when policy reads should hit the AMS instead of the mirror."""
    return os.environ.get("HERMES_AMS_LIVE_READS", "").lower() in ("1", "true", "yes")


def _ttl() -> int:
    try:
        return max(0, int(os.environ.get("HERMES_AMS_BOOK_TTL", DEFAULT_TTL_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS


def invalidate_cache() -> None:
    """Drop the cached book (call after a write that should be visible now)."""
    with _lock:
        _cache["rows"], _cache["at"] = None, 0.0
        # Clear the backoff too — an explicit invalidation is a request to go and
        # look again, which a lingering failure stamp would otherwise refuse.
        _cache["failed_at"] = 0.0
        _cache["refreshing"] = False
        _cache["thread"] = None
        _cache["full_at"] = 0.0


def _lineage_index(supa: "SupabaseClient") -> dict[str, str]:
    """policy_guid -> renewed_policy. Best-effort: lineage is an enrichment, and
    losing it must not take the whole book read down with it."""
    try:
        rows = supa.select(
            LINEAGE_TABLE, columns=f"{POLICY_KEY},{LINEAGE_FIELD}", limit=20000
        )
    except Exception:  # noqa: BLE001
        log.exception("ams.book: lineage lookup failed; serving without lineage")
        return {}
    return {
        str(r.get(POLICY_KEY)): r[LINEAGE_FIELD]
        for r in rows
        if r.get(POLICY_KEY) and r.get(LINEAGE_FIELD)
    }


def fetch_book(
    supa: "SupabaseClient",
    *,
    nowcerts: "NowCertsClient | None" = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """The whole live book, shaped like ``canonical_policies`` rows.

    Cached for ``HERMES_AMS_BOOK_TTL`` seconds. **A request never waits on the
    AMS** — not on a stale cache, and not on a cold one.

    The earlier version only avoided blocking once something was cached, and let
    a cold cache block "just this once". Against an AMS that is failing rather
    than merely slow, "just this once" is every single request: the pull never
    succeeds, so the cache never fills, so every caller pays the full timeout.
    Raising the per-read timeout to 60s then made each stall *longer* — measured
    77s on /api/clients — which is the opposite of the intent.

    So: cold cache raises immediately and starts a background pull. Callers go
    through ``select_policies``, which falls back to the Supabase mirror on any
    exception, so the CRM stays responsive on mirror data and silently upgrades
    to live the moment a background pull lands. ``force=True`` still blocks — it
    is only used by jobs that genuinely need the freshest book.
    """
    ttl = _ttl()
    with _lock:
        rows = _cache["rows"]
        fresh = rows is not None and (time.time() - _cache["at"]) < ttl
        if fresh and not force:
            return rows
        failed_at = _cache.get("failed_at") or 0.0
        backing_off = (time.time() - failed_at) < _FAIL_BACKOFF_SECONDS
        # Decide under the lock; act outside it. Calling out while holding a
        # non-reentrant lock is how a refresh that runs inline deadlocks.
        spawn = not force and not backing_off and not _cache["refreshing"]
        if spawn:
            _cache["refreshing"] = True

    if spawn:
        _spawn_refresh(supa, nowcerts)

    if not force:
        if rows is not None:
            return rows              # stale beats slow
        with _lock:                  # an inline refresh may have just filled it
            rows = _cache["rows"]
        if rows is not None:
            return rows
        raise AmsBookUnavailable(
            "ams.book: no cached book yet"
            + (f"; last read failed, backing off {_FAIL_BACKOFF_SECONDS}s" if backing_off else "")
            + " — serving the mirror while the AMS pull runs in the background"
        )

    return _pull_book(supa, nowcerts)


def _spawn_refresh(
    supa: "SupabaseClient", nowcerts: "NowCertsClient | None"
) -> None:
    """Refresh the book off the request path. Caller holds _lock and has already
    set ``refreshing``."""

    def _run() -> None:
        try:
            _pull_book(supa, nowcerts)
        except Exception:  # noqa: BLE001 — a background refresh must never escape
            log.warning("ams.book: background refresh failed; serving stale", exc_info=True)
        finally:
            with _lock:
                _cache["refreshing"] = False

    t = threading.Thread(target=_run, name="ams-book-refresh", daemon=True)
    _cache["thread"] = t
    t.start()


def await_refresh(timeout: float = 120.0) -> None:
    """Block until any in-flight background refresh settles. For tests and for
    callers (the nightly sync) that genuinely need the freshest book."""
    t = _cache.get("thread")
    if t is not None:
        t.join(timeout)


def _pull_book(
    supa: "SupabaseClient",
    nowcerts: "NowCertsClient | None" = None,
) -> list[dict[str, Any]]:
    """The actual AMS pull + cache write. Raises on failure.

    Incremental once the book is warm. A full pull is 5 sequential pages against
    an API measured at ~40% success per page, so it lands ~1% of the time raw and
    ~79% even with retries. A ``changeDate ge`` delta is normally a single page —
    ~95% with retries — and finishes in seconds instead of minutes.

    A delta cannot report a *deletion*, so a full pull is forced every
    ``_FULL_REFRESH_SECONDS`` to re-sync removals. Everything in between rides
    the delta.
    """
    if nowcerts is None:
        # The SHARED client, not a fresh one. Most callers of select_policies()
        # don't thread a client through (15 call sites, nearly none of them do),
        # so building one here meant a brand-new empty token cache — and a fresh
        # ~26s password grant — on every single book read.
        from hermes.sync.nowcerts_client import get_client

        nowcerts = get_client()

    # A failed AMS pull used to propagate straight out of here, before the cache
    # write below — so nothing was ever recorded, the TTL never engaged, and every
    # subsequent request re-paid the full timeout. Stamp the attempt first: a
    # failure now backs off for _FAIL_BACKOFF_SECONDS instead of hammering a sick
    # upstream once per widget.
    with _lock:
        have = _cache["rows"]
        full_age = time.time() - (_cache.get("full_at") or 0.0)
    # Delta only when there is something to apply it to, and only until the
    # periodic full pull is due — a delta never reports a deleted policy.
    since = None
    if have is not None and full_age < _full_refresh_secs():
        since = _iso(_cache.get("at") or 0.0)

    try:
        records = nowcerts.fetch_policies(
            page_size=_PAGE_SIZE, max_pages=_MAX_PAGES, since=since
        )
    except Exception:
        with _lock:
            _cache["failed_at"] = time.time()
        raise
    lineage = _lineage_index(supa)

    rows: list[dict[str, Any]] = []
    for p in records:
        guid = _policy_guid(p)
        if not guid:
            continue  # no stable key -> cannot be addressed or reconciled
        row: dict[str, Any] = {POLICY_KEY: guid, **_map_policy_volatile(p)}
        # Not in the shared volatile mapper, but commission_sync reads it and the
        # AMS supplies it as ``totalAgencyCommission``. Pick by presence, not
        # truthiness — a real 0.0 commission must stay 0.0 rather than collapse to
        # None and send commission_sync off to its rule-based fallback.
        commission = next(
            (p[k] for k in ("totalAgencyCommission", "agencyCommission") if p.get(k) is not None),
            None,
        )
        row["agency_commission_amount"] = _num(commission)
        row[LINEAGE_FIELD] = lineage.get(guid)
        rows.append(row)

    now = time.time()
    with _lock:
        if since is not None and _cache["rows"] is not None:
            merged = {r[POLICY_KEY]: r for r in _cache["rows"]}
            merged.update({r[POLICY_KEY]: r for r in rows})
            rows = list(merged.values())
            log.info(
                "ams.book: %s changed since %s; book now %s policies", len(records), since, len(rows)
            )
        else:
            _cache["full_at"] = now
            log.info("ams.book: %s policies live (%s with lineage)", len(rows), len(lineage))
        _cache["rows"], _cache["at"] = rows, now
        _cache["failed_at"] = 0.0  # a good read retires any outstanding backoff
    return rows


# ---------------------------------------------------------------------------
# PostgREST-compatible filtering, so call sites swap without changing shape
# ---------------------------------------------------------------------------
def _matches(row: dict[str, Any], field: str, expr: str) -> bool:
    op, _, raw = str(expr).partition(".")
    val = row.get(field)
    if op == "eq":
        return str(val) == raw
    if op == "neq":
        return str(val) != raw
    if op == "in":
        return str(val) in {v.strip().strip('"') for v in raw.strip("()").split(",")}
    if op == "is":
        return val is None if raw == "null" else val is not None
    # An unsupported operator must not silently widen the result set.
    raise ValueError(f"ams.book: unsupported filter {field}={expr}")


def _sort(rows: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    field, _, direction = order.partition(".")
    desc = direction.startswith("desc")
    # None sorts last in both directions — matches PostgREST's default NULLS LAST.
    return sorted(
        rows,
        key=lambda r: (r.get(field) is None, r.get(field) if r.get(field) is not None else ""),
        reverse=desc,
    )


def select_policies(
    supa: "SupabaseClient",
    *,
    columns: str | None = None,
    params: dict[str, Any] | None = None,
    limit: int | None = None,
    nowcerts: "NowCertsClient | None" = None,
) -> list[dict[str, Any]]:
    """Drop-in for ``supa.select("canonical_policies", ...)``.

    Falls back to Supabase unless ``HERMES_AMS_LIVE_READS`` is set, and also on
    any AMS failure — a degraded read beats a broken portal.
    """
    params = dict(params or {})
    if not live_reads_enabled():
        return supa.select(POLICIES_TABLE, columns=columns, params=params, limit=limit)

    try:
        rows = fetch_book(supa, nowcerts=nowcerts)
    except Exception:  # noqa: BLE001
        log.exception("ams.book: live read failed; falling back to the mirror")
        return supa.select(POLICIES_TABLE, columns=columns, params=params, limit=limit)

    order = params.pop("order", None)
    for field, expr in params.items():
        rows = [r for r in rows if _matches(r, field, expr)]
    if order:
        rows = _sort(rows, str(order))
    if limit is not None:
        rows = rows[:limit]

    if columns and columns != "*":
        wanted = [c.strip() for c in columns.split(",") if c.strip()]
        rows = [{c: r.get(c) for c in wanted} for r in rows]
    return rows
