"""Hermes REST API — lightweight FastAPI wrapper around the Dispatcher.

Start with: hermes --api  (or: uvicorn hermes.api:app --host 0.0.0.0 --port 8484)

Open WebUI, n8n, or any HTTP client can call POST /dispatch with a JSON body:
    {"command": "sync nowcerts dry-run"}
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger(__name__)

app = FastAPI(
    title="Hermes API",
    description="EspoCRM coordination middleware — sync, lookup, data quality, and more.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Lazy singletons (initialized on first request) ─────────────────────────

_espo = None
_dispatcher = None


def _get_espo():
    global _espo
    if _espo is None:
        from hermes.core.client import EspoClient
        _espo = EspoClient()
    return _espo


def _get_dispatcher():
    global _dispatcher
    if _dispatcher is None:
        from hermes.core.dispatcher import Dispatcher
        use_openai = bool(
            os.environ.get("OPENAI_API_KEY") or os.environ.get("HERMES_OPENAI_API_KEY")
        )
        _dispatcher = Dispatcher(use_openai=use_openai)
    return _dispatcher


# ── Request / Response models ───────────────────────────────────────────────

class DispatchRequest(BaseModel):
    command: str


class DispatchResponse(BaseModel):
    ok: bool
    message: str
    data: dict | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "hermes"}


@app.post("/dispatch", response_model=DispatchResponse)
async def dispatch(req: DispatchRequest):
    """Route a natural-language command through the Hermes Dispatcher.

    Examples:
        {"command": "sync nowcerts dry-run"}
        {"command": "sync status"}
        {"command": "find account Acme"}
        {"command": "data quality"}
        {"command": "ping"}
    """
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="Empty command.")

    try:
        espo = _get_espo()
        dispatcher = _get_dispatcher()
        result = dispatcher.dispatch(espo, req.command)
        return DispatchResponse(ok=result.ok, message=result.message, data=result.data)
    except Exception as exc:
        log.exception("Dispatch failed for command: %s", req.command)
        raise HTTPException(status_code=500, detail=str(exc))
