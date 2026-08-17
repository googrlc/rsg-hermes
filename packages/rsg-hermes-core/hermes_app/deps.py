"""Shared request plumbing for the routers.

These were module-level helpers in `hermes/api.py`, which meant a route could
not leave that file without leaving its dependencies behind. They live here so
every router reaches them the same way and moving a route between modules is
not also a rewrite of how it gets a database handle.

The clients are process-wide singletons, built lazily on first use so importing
a router never opens a connection or spends a NowCerts password grant.

The natural-language Dispatcher is deliberately NOT here. It is the hub app's
router, and a shared layer that constructs one would make every app repo depend
on the hub — tests/test_core_is_a_leaf.py caught exactly that. It lives in
hermes/api.py.

`hermes.api` keeps thin delegators (`_get_supa` and friends) that call straight
through to these, so its existing call sites — and the tests that patch them —
are unaffected.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

_supa = None
_nowcerts = None

# Route handlers with synchronous bodies are declared `def`, so FastAPI runs
# them in a threadpool and several can be in flight at once. That makes the
# lazy `if _x is None: _x = ...` below a race: two threads can both see None and
# both construct. For NowCerts that is not just a wasted object, it is a second
# ~26s password grant. One lock for all three — they are built once at startup
# and never contended after.
_init_lock = threading.Lock()


def model_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()  # type: ignore[no-any-return]


def get_supa():
    global _supa
    if _supa is None:
        with _init_lock:
            if _supa is None:
                from hermes_integrations.supabase_client import SupabaseClient

                _supa = SupabaseClient()
    return _supa


def get_nowcerts():
    """The shared NowCertsClient. Reads NOWCERTS_USERNAME/PASSWORD from env.

    Delegates to ``nowcerts_client.get_client()`` rather than keeping a second
    singleton of its own: two singletons meant two tokens and two ~26s password
    grants in one process, and the API and the book reads each paying their own.
    """
    global _nowcerts
    if _nowcerts is None:
        with _init_lock:
            if _nowcerts is None:
                from hermes_integrations.nowcerts_client import get_client

                _nowcerts = get_client()
    return _nowcerts


def reset_clients() -> None:
    """Drop the cached clients. For tests, which build fakes per case."""
    global _supa, _nowcerts
    _supa = _nowcerts = None


def require_hermes_token(request: Request) -> None:
    """Bearer-token gate for mutating / privileged endpoints.

    Reads HERMES_API_TOKEN from env. If unset, the gate is disabled (dev mode);
    log a warning so it's visible.
    """
    expected = os.environ.get("HERMES_API_TOKEN")
    if not expected:
        log.warning("HERMES_API_TOKEN not set; bearer gate disabled")
        return
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def active_user_emails(supa) -> set[str]:
    rows = supa.select(
        "agency_crm_users", columns="email",
        params={"active": "eq.true"}, limit=1000,
    )
    return {str(r.get("email")).lower() for r in rows if r.get("email")}


def require_users(supa, pairs: list[tuple[str, str | None]]) -> None:
    """Reject any *_email that isn't an active agency_crm_users identity.

    This is the API-level guard for the FK that made CRM task creation fail
    silently — the cockpit picks emails from /api/agency-users, never free-typed.
    """
    valid = active_user_emails(supa)
    for label, email in pairs:
        if email and email.lower() not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"{label} '{email}' is not an active agency_crm_users identity",
            )


def user_role(supa, email: str) -> str | None:
    """Active ``agency_crm_users.role`` for ``email``, or None if not found."""
    needle = (email or "").strip().lower()
    if not needle:
        return None
    rows = supa.select(
        "agency_crm_users",
        columns="role",
        params={"email": f"eq.{needle}", "active": "eq.true"},
        limit=1,
    )
    if not rows:
        return None
    return str(rows[0].get("role") or "")


def require_administrator(
    supa,
    email: str | None,
    *,
    field_label: str = "saved_by",
) -> None:
    """Settings-save gate — Lamar (``administrator``) only per identity matrix.

    Gretchen and other operators may read settings surfaces but cannot mutate
    commission rules, registry config, schedules, or credentials.
    """
    if not (email or "").strip():
        raise HTTPException(status_code=400, detail=f"{field_label} is required")
    require_users(supa, [(field_label, email)])
    role = user_role(supa, email)
    if role != "administrator":
        raise HTTPException(
            status_code=403,
            detail=(
                f"{field_label} '{email}' cannot save settings — "
                "administrator role required"
            ),
        )


class ExecutorRunRequest(BaseModel):
    """How much of a queue to drain, and whether to only preview it.

    Shared by every "run the executor" endpoint (/api/casework/run,
    /api/intake/run) — they take the same two knobs, so the shape lives here
    rather than in whichever router happened to define it first. It was
    `CaseworkRunRequest` in api.py and the intake route borrowed it, which is
    how a casework-named model ended up in the intake contract.
    """

    limit: int = 5
    dry_run: bool = False
