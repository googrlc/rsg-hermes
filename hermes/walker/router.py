"""Walker FastAPI router -- on-demand renewal endpoints for the custom GPT.

All endpoints require a bearer token (WALKER_API_TOKEN or HERMES_API_TOKEN).
Every READ response carries a "data_as_of" freshness stamp.
"""
from __future__ import annotations

import logging
import os
import hmac
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .service import WalkerService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/walker", tags=["walker"])

_walker: WalkerService | None = None


def _get_walker() -> WalkerService:
    global _walker
    if _walker is None:
        _walker = WalkerService()
    return _walker


def reset_walker():
    """Reset the singleton (for tests)."""
    global _walker
    _walker = None


def _require_walker_token(request: Request) -> None:
    """Bearer-token gate. Uses WALKER_API_TOKEN, falls back to HERMES_API_TOKEN."""
    expected = os.environ.get("WALKER_API_TOKEN") or os.environ.get("HERMES_API_TOKEN")
    if not expected:
        log.warning("WALKER_API_TOKEN / HERMES_API_TOKEN not set; bearer gate disabled")
        return
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing walker token")


# -- READS ------------------------------------------------------------------

@router.get("/queue")
async def get_queue(request: Request, days: int = 60):
    """Renewals inside N days, classified at request time. Freshness stamped."""
    _require_walker_token(request)
    try:
        return _get_walker().get_queue(days=days)
    except Exception as exc:
        log.exception("walker queue failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/renewal/{renewal_id}")
async def get_renewal_detail(request: Request, renewal_id: str):
    """Single-client truth pulled LIVE from NowCerts + EspoCRM touch history."""
    _require_walker_token(request)
    try:
        return _get_walker().get_renewal_detail(renewal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.exception("walker renewal detail failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/search")
async def search(request: Request, q: str = ""):
    """Lookup by name or policy number. Finds name variants via ilike."""
    _require_walker_token(request)
    return _get_walker().search(q)


@router.get("/quiet-lapse")
async def get_quiet_lapse(request: Request):
    """Expired terms with NO successor term. The silent-churn catcher."""
    _require_walker_token(request)
    try:
        return _get_walker().get_quiet_lapse()
    except Exception as exc:
        log.exception("walker quiet-lapse failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/scoreboard")
async def get_scoreboard(request: Request):
    """Retention %, renewed/lost premium."""
    _require_walker_token(request)
    try:
        return _get_walker().get_scoreboard()
    except Exception as exc:
        log.exception("walker scoreboard failed")
        raise HTTPException(status_code=500, detail=str(exc))


# -- WRITES (all land on the EspoCRM Opportunity) ---------------------------

class TouchRequest(BaseModel):
    actor: str = "lamar"
    channel: str = "manual"
    note: str = ""


class WorksheetRequest(BaseModel):
    fields: dict[str, Any]


class FlagRequest(BaseModel):
    flag: str


class HandoffRequest(BaseModel):
    note: str


class OutcomeRequest(BaseModel):
    decision: str
    stage: str | None = None


@router.post("/touch/{id}")
async def post_touch(request: Request, id: str, body: TouchRequest):
    """Log a touch on the Opportunity."""
    _require_walker_token(request)
    try:
        return _get_walker().post_touch(id, body.model_dump())
    except Exception as exc:
        log.exception("walker touch failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/worksheet/{id}")
async def patch_worksheet(request: Request, id: str, body: WorksheetRequest):
    """Update worksheet state on the Opportunity."""
    _require_walker_token(request)
    try:
        return _get_walker().patch_worksheet(id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("walker worksheet failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/flag/{id}")
async def post_flag(request: Request, id: str, body: FlagRequest):
    """Add a complexity flag to the Opportunity."""
    _require_walker_token(request)
    try:
        return _get_walker().post_flag(id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("walker flag failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/handoff/{id}")
async def post_handoff(request: Request, id: str, body: HandoffRequest):
    """Set handoff notes on the Opportunity."""
    _require_walker_token(request)
    try:
        return _get_walker().post_handoff(id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("walker handoff failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/outcome/{id}")
async def post_outcome(request: Request, id: str, body: OutcomeRequest):
    """Set the renewal decision + pipeline stage."""
    _require_walker_token(request)
    try:
        return _get_walker().post_outcome(id, body.model_dump())
    except Exception as exc:
        log.exception("walker outcome failed")
        raise HTTPException(status_code=500, detail=str(exc))
